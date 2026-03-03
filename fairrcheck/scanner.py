"""
scanner.py — Orchestrates FAIRR evaluation of a project folder.

Flow
----
1. Load registry from YAML (single source of truth).
2. For each metric:
   - If implemented_in_prototype=True: run deterministic detector.
   - If implemented_in_prototype=False: stub entry (score=null).
3. Optionally augment with LLM evaluation (--llm flag).
4. Compute aggregate scores.
5. Return structured result dict.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .detectors import DETECTOR_MAP, DetectorResult
from .llm import LLMConfig, llm_evaluate_metric
from .registry import MetricSpec, Registry, load_registry
from .scoring import compute_scores

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 100_000   # HPC constraint: skip files > 100 KB for LLM


# ---------------------------------------------------------------------------
# File excerpt collector (for LLM augmentation)
# ---------------------------------------------------------------------------

_INTERESTING_FILES = [
    "README.md", "README.rst", "README.txt", "README",
    "CITATION.cff", "codemeta.json", "metadata.json",
    "pyproject.toml", "requirements.txt", "environment.yml",
    "LICENSE", "LICENSE.md", "LICENSE.txt",
    "Dockerfile", "Singularity", "Apptainer",
]

_SENSITIVE_PATTERNS = [".env", "secrets", "credentials", "id_rsa", ".pem", ".key"]


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return any(pat in name for pat in _SENSITIVE_PATTERNS)


def collect_excerpts(project_path: Path) -> Dict[str, str]:
    """Collect text snippets from key project files for LLM context."""
    excerpts: Dict[str, str] = {}
    for fname in _INTERESTING_FILES:
        fpath = project_path / fname
        if fpath.exists() and fpath.is_file() and not _is_sensitive(fpath):
            size = fpath.stat().st_size
            if size > _MAX_FILE_BYTES:
                logger.debug("Skipping %s (size %d > %d)", fname, size, _MAX_FILE_BYTES)
                continue
            try:
                excerpts[fname] = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.debug("Could not read %s: %s", fname, exc)
    return excerpts


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------


def _evaluate_metric(
    metric: MetricSpec,
    project_path: Path,
    mode: str,
    use_llm: bool,
    llm_config: Optional[LLMConfig],
    excerpts: Optional[Dict[str, str]],
    max_score: int,
    on_llm_start: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """Evaluate a single metric and return the result dict."""

    base: Dict[str, Any] = {
        "metric_id": metric.id,
        "name": metric.name,
        "principle": metric.principle,
        "implemented": metric.implemented_in_prototype,
        "score": None,
        "max_score": max_score,
        "evidence": [],
        "rationale": "",
        "llm_used": False,
        "notes": "",
    }

    if not metric.implemented_in_prototype:
        base["notes"] = "Not implemented in prototype"
        return base

    detector = DETECTOR_MAP.get(metric.id)
    if detector is None:
        base["notes"] = f"Detector mapped as implemented but function missing for {metric.id}"
        base["score"] = 0
        return base

    # --- deterministic pass ---
    try:
        det: DetectorResult = detector(project_path, mode)
    except Exception as exc:
        logger.warning("Detector for %s raised: %s", metric.id, exc)
        det = DetectorResult(score=0, evidence=[], rationale=f"Detector error: {exc}")

    base["score"] = det.score
    base["evidence"] = det.evidence
    base["rationale"] = det.rationale

    # --- optional LLM augmentation ---
    if use_llm and llm_config and llm_config.is_configured and excerpts:
        if det.score < max_score:  # only call LLM if there's room to improve
            if on_llm_start:
                on_llm_start(metric.id, metric.principle)
            try:
                llm_result = llm_evaluate_metric(
                    config=llm_config,
                    metric_id=metric.id,
                    metric_name=metric.name,
                    metric_description=metric.description,
                    project_path=project_path,
                    file_excerpts=excerpts,
                    deterministic_score=det.score,
                    max_score=max_score,
                )
                suggestion = llm_result.get("score_suggestion", det.score)
                # Safety: never reduce
                final_score = max(det.score, int(suggestion))
                if final_score > det.score:
                    base["score"] = final_score
                    base["rationale"] = (
                        f"[LLM upgrade] {llm_result.get('reasoning', '')}"
                    )
                    base["evidence"].append(
                        f"LLM evidence: {llm_result.get('evidence_excerpt', '')}"
                    )
                    base["llm_used"] = True
            except Exception as exc:
                logger.warning("LLM eval for %s failed: %s", metric.id, exc)

    return base


def run_scan(
    project_path: Path,
    mode: str = "development",
    use_llm: bool = False,
    llm_config: Optional[LLMConfig] = None,
    registry: Optional[Registry] = None,
    registry_path: Optional[Path] = None,
    on_metric_start: Optional[Callable[[str, str, int, int], None]] = None,
    on_llm_start: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Run a full FAIRR scan on *project_path*.

    Parameters
    ----------
    project_path:    Directory to scan.
    mode:            "development" | "publication"
    use_llm:         Whether to augment deterministic scores with LLM.
    llm_config:      LLM configuration.  Required if use_llm=True.
    registry:        Pre-loaded Registry; if None, loaded from *registry_path*.
    registry_path:   Override path to YAML registry.
    on_metric_start: Optional callback(metric_id, principle, index, total) called
                     before each metric is evaluated.
    on_llm_start:    Optional callback(metric_id, principle) called immediately
                     before each LLM API request.

    Returns
    -------
    Full result dict ready for reporting.
    """
    project_path = Path(project_path).resolve()
    if not project_path.exists():
        raise FileNotFoundError(f"Project path does not exist: {project_path}")

    reg = registry or load_registry(registry_path)
    max_score = reg.max_score
    excerpts: Optional[Dict[str, str]] = None
    if use_llm:
        excerpts = collect_excerpts(project_path)

    metric_results: List[Dict[str, Any]] = []
    total_metrics = len(reg.metrics)
    for idx, metric in enumerate(reg.metrics):
        if on_metric_start:
            on_metric_start(metric.id, metric.principle, idx, total_metrics)
        result = _evaluate_metric(
            metric=metric,
            project_path=project_path,
            mode=mode,
            use_llm=use_llm,
            llm_config=llm_config,
            excerpts=excerpts,
            max_score=max_score,
            on_llm_start=on_llm_start,
        )
        metric_results.append(result)

    scores = compute_scores(metric_results, reg)

    return {
        "schema_version": reg.schema_version,
        "registry_name": reg.name,
        "project_path": str(project_path),
        "project_name": project_path.name,
        "scan_mode": mode,
        "llm_used": use_llm,
        "llm_model": llm_config.model if (use_llm and llm_config) else None,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metric_results,
        "max_score": max_score,
        "principles": scores["principles"],
        "overall_fairr_score": scores["overall_fairr_score"],
        "grade": scores["grade"],
        "score_summary": scores["score_summary"],
    }
