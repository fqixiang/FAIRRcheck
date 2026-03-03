"""
agent.py — Aider-style fix agent for fairrcheck.

Implements the safe patch workflow:
  1. Generate unified diff via LLM (or subprocess Aider).
  2. Show the diff to the user.
  3. If --apply and user confirms, apply the patch.

Allowed files (can be extended via config):
    README.md, CITATION.cff, codemeta.json, metadata.json, .zenodo.json,
    LICENSE, LICENSE.md, LICENSE.txt, CONTRIBUTING.md,
    requirements.txt, environment.yml, environment.yaml,
    Dockerfile, .dockerignore, Makefile
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLMConfig, llm_generate_patch

logger = logging.getLogger(__name__)

# Files the fix agent is allowed to create or modify
ALLOWED_FILES = {
    # Documentation / description
    "README.md",
    "CONTRIBUTING.md",
    # Citation & metadata
    "CITATION.cff",
    "codemeta.json",
    "metadata.json",
    ".zenodo.json",
    # Licensing
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    # Dependencies / environment
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    # Container / build
    "Dockerfile",
    ".dockerignore",
    "Makefile",
}


# ---------------------------------------------------------------------------
# Aider subprocess helper
# ---------------------------------------------------------------------------


def _aider_available() -> bool:
    try:
        result = subprocess.run(
            ["aider", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _run_aider(
    project_path: Path,
    message: str,
    files: List[Path],
    dry_run: bool = True,
) -> Optional[str]:
    """
    Call Aider via subprocess to generate/apply a patch.
    Returns stdout (the diff) if dry_run=True, or None on failure.
    """
    cmd = [
        "aider",
        "--no-auto-commits",
        "--yes",
        "--no-gitignore",
        "--no-show-model-warnings",
        "--message", message,
    ]
    if dry_run:
        cmd.append("--dry-run")
    for f in files:
        cmd.extend(["--file", str(f)])

    # Require an explicit model; auto-prepend "openai/" for OpenAI-compatible endpoints.
    raw_model = os.environ.get("FAIRRCHECK_LLM_MODEL", "")
    if not raw_model:
        logger.warning("Aider skipped: FAIRRCHECK_LLM_MODEL is not set.")
        return None
    model = raw_model if raw_model.startswith("openai/") else f"openai/{raw_model}"
    cmd.extend(["--model", model])

    env = os.environ.copy()
    if os.environ.get("FAIRRCHECK_LLM_BASE_URL"):
        env.setdefault("OPENAI_API_BASE", os.environ["FAIRRCHECK_LLM_BASE_URL"])
    if os.environ.get("FAIRRCHECK_LLM_API_KEY"):
        env.setdefault("OPENAI_API_KEY", os.environ["FAIRRCHECK_LLM_API_KEY"])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            logger.warning("Aider exited with code %d: %s", result.returncode, result.stderr[:500])
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.error("Aider timed out after 30s")
        return None
    except Exception as exc:
        logger.error("Aider failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Patch validation
# ---------------------------------------------------------------------------


def _validate_unified_diff(diff: str, allowed_files: set) -> List[str]:
    """
    Return list of problems found in *diff*.

    Rules:
    - New files (source is /dev/null) are always allowed.
    - Modifications to existing files are only allowed if the filename is in
      allowed_files.
    - We only inspect +++ lines (the destination); --- lines are the source
      (either /dev/null for new files, or the old version of an existing file).
    """
    problems = []
    # Track whether the current file hunk is a new-file creation.
    source_is_devnull = False
    for line in diff.splitlines():
        if line.startswith("--- "):
            parts = line[4:].split("\t")[0].strip()
            source_is_devnull = parts in ("/dev/null", "dev/null")
        elif line.startswith("+++ "):
            parts = line[4:].split("\t")[0].strip()
            # Strip leading a/ b/ ./ prefixes
            for prefix in ("a/", "b/", "./"):
                if parts.startswith(prefix):
                    parts = parts[len(prefix):]
            fname = Path(parts).name
            # Deleting a file (destination is /dev/null) — always safe
            if fname in ("null",) and parts.endswith("/dev/null") or parts == "/dev/null":
                continue
            # New file creation — always allowed
            if source_is_devnull:
                continue
            # Modification of existing file — must be in allowed list
            if fname not in allowed_files:
                problems.append(f"Patch modifies disallowed file: {fname}")
    return problems


def _apply_patch(project_path: Path, diff: str) -> bool:
    """Apply a unified diff via the `patch` CLI. Returns True on success."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".patch", delete=False, dir=project_path
    ) as tmp:
        tmp.write(diff)
        tmp_path = Path(tmp.name)

    try:
        result = subprocess.run(
            ["patch", "-p0", "-i", str(tmp_path)],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("patch failed: %s", result.stderr)
            return False
        logger.info("Patch applied successfully.")
        return True
    except Exception as exc:
        logger.error("Failed to apply patch: %s", exc)
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class FixAgent:
    """
    Fix agent for fairrcheck.

    Default strategy: LLM-generated unified diff.
    Optional strategy: subprocess Aider (pass use_aider=True).
    """

    def __init__(
        self,
        project_path: Path,
        llm_config: LLMConfig,
        allowed_files: Optional[set] = None,
        use_aider: bool = False,
    ) -> None:
        self.project_path = project_path
        self.llm_config = llm_config
        self.allowed_files = allowed_files or ALLOWED_FILES
        self.use_aider = use_aider

    def _collect_target_files(self, existing_only: bool = False) -> List[Path]:
        """Return paths for allowed files. Aider can create new ones, so by default
        all allowed paths are returned; pass existing_only=True to filter to those
        that already exist on disk."""
        return [
            self.project_path / f
            for f in self.allowed_files
            if not existing_only or (self.project_path / f).exists()
        ]

    def generate(
        self, suggestions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Generate patches for each suggestion.

        Returns list of dicts: {metric_id, filename, diff, problems, method}.
        """
        patches: List[Dict[str, Any]] = []

        for suggestion in suggestions:
            metric_id = suggestion.get("metric_id", "unknown")
            message = suggestion.get("message", "")
            # Aider receives all allowed paths (it can create new files);
            # the LLM fallback below filters to existing files first.
            target_files = self._collect_target_files(existing_only=True)

            diff = ""
            method = "none"

            if self.use_aider and _aider_available():
                logger.info("Using Aider for %s", metric_id)
                output = _run_aider(
                    self.project_path,
                    f"[FAIRR {metric_id}] {message}",
                    target_files,
                    dry_run=True,
                )
                if output:
                    diff = output
                    method = "aider"

            if not diff:
                logger.info("Using LLM fallback patch for %s", metric_id)
                # Prefer existing files; fall back to creating README.md from scratch.
                preferred = [
                    "README.md", "CITATION.cff", "codemeta.json", "metadata.json",
                    ".zenodo.json", "LICENSE", "requirements.txt", "environment.yml",
                ]
                chosen_fname: Optional[str] = None
                chosen_content: str = ""
                for fname in preferred:
                    target = self.project_path / fname
                    if target.exists():
                        chosen_fname = fname
                        chosen_content = target.read_text(encoding="utf-8", errors="replace")
                        break
                if chosen_fname is None:
                    # No existing target — ask LLM to create README.md
                    chosen_fname = "README.md"
                    chosen_content = ""
                diff = llm_generate_patch(
                    self.llm_config, metric_id, message, chosen_content, chosen_fname
                )
                method = "llm_patch"

            if not diff:
                patches.append(
                    {
                        "metric_id": metric_id,
                        "filename": None,
                        "diff": "",
                        "problems": ["Could not generate patch"],
                        "method": "none",
                    }
                )
                continue

            problems = _validate_unified_diff(diff, self.allowed_files)
            patches.append(
                {
                    "metric_id": metric_id,
                    "filename": None,
                    "diff": diff,
                    "problems": problems,
                    "method": method,
                }
            )

        return patches

    def apply(self, patch_info: Dict[str, Any]) -> bool:
        """Apply a single patch dict (as returned by generate())."""
        if patch_info.get("problems"):
            logger.error(
                "Refusing to apply patch with problems: %s", patch_info["problems"]
            )
            return False
        diff = patch_info.get("diff", "")
        if not diff:
            logger.warning("Empty diff; nothing to apply.")
            return False
        return _apply_patch(self.project_path, diff)
