"""
detectors.py — Deterministic heuristic detectors for FAIRR metrics.

Each detector is a plain function with the signature:
    def detect_<METRIC_ID_NORMALISED>(path: Path, mode: str) -> DetectorResult

The registry drives which detectors are called; nothing here re-declares
metric lists — it only provides the detection logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class DetectorResult:
    score: int                      # 0 | 1 | 2
    evidence: List[str] = field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_README_PATTERNS = re.compile(r"^readme(\.(md|txt|rst))?$", re.IGNORECASE)


def _find_readme(path: Path) -> Optional[Path]:
    for f in path.iterdir():
        if f.is_file() and _README_PATTERNS.match(f.name):
            return f
    return None


def _read_text_safe(p: Path, max_bytes: int = 50_000) -> str:
    """Read text from *p*, capped at *max_bytes* to keep things fast."""
    try:
        data = p.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _files_matching(path: Path, *patterns: str) -> List[Path]:
    """Return files in *path* (non-recursive) whose names match any pattern."""
    results = []
    for p in path.iterdir():
        if p.is_file() and any(
            re.search(pat, p.name, re.IGNORECASE) for pat in patterns
        ):
            results.append(p)
    return results


def _glob_exists(path: Path, glob: str) -> List[Path]:
    return list(path.glob(glob))


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


def detect_FAIRR_F2(path: Path, mode: str) -> DetectorResult:
    """Core descriptive metadata: README + title/description/creators."""
    evidence: List[str] = []
    score = 0

    # Candidate metadata files
    candidates = [
        path / "CITATION.cff",
        path / "codemeta.json",
        path / "metadata.json",
    ]
    readme = _find_readme(path)
    if readme:
        candidates.append(readme)

    found_files = [c for c in candidates if c.exists()]
    for f in found_files:
        evidence.append(f"Found: {f.name}")

    if not found_files:
        return DetectorResult(
            score=0,
            evidence=[],
            rationale="No README or metadata file found.",
        )

    # Check keyword richness across all found files
    combined_text = "\n".join(_read_text_safe(f) for f in found_files).lower()
    has_title = any(
        kw in combined_text for kw in ("title:", "# ", "name:", "project")
    )
    has_desc = any(
        kw in combined_text
        for kw in ("description:", "abstract:", "summary", "description")
    )
    has_creator = any(
        kw in combined_text
        for kw in ("author", "creator", "maintainer", "contributors")
    )

    checks_passed = sum([has_title, has_desc, has_creator])

    if checks_passed == 3:
        score = 2
        rationale = "Title, description and creator fields detected."
    elif checks_passed >= 1:
        score = 1
        rationale = f"Partial metadata: {checks_passed}/3 fields detected."
    else:
        score = 0
        rationale = "Metadata files exist but key fields (title/description/creator) missing."

    if has_title:
        evidence.append("title-like field detected")
    if has_desc:
        evidence.append("description-like field detected")
    if has_creator:
        evidence.append("creator/author field detected")

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_F4(path: Path, mode: str) -> DetectorResult:
    """Machine-readable metadata: JSON/YAML/JSON-LD/RO-Crate/CITATION."""
    evidence: List[str] = []

    structured_files = _files_matching(
        path,
        r"CITATION\.cff$",
        r"codemeta\.json$",
        r"metadata\.json$",
        r"ro-crate-metadata\.json(ld)?$",
        r"\.ya?ml$",
        r"\.json$",
    )
    for f in structured_files[:5]:  # show up to 5
        evidence.append(f"Structured file: {f.name}")

    if not structured_files:
        return DetectorResult(
            score=0,
            evidence=[],
            rationale="No structured machine-readable metadata files found.",
        )

    # Distinguish high-quality (CITATION, codemeta, RO-Crate) from generic
    high_quality = [
        f
        for f in structured_files
        if re.search(r"CITATION|codemeta|ro-crate|metadata\.json", f.name, re.I)
    ]
    score = 2 if high_quality else 1
    rationale = (
        f"High-quality metadata ({', '.join(f.name for f in high_quality)})."
        if high_quality
        else "Generic structured files found; no dedicated metadata descriptor."
    )
    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_A1(path: Path, mode: str) -> DetectorResult:
    """Access conditions documented in README."""
    evidence: List[str] = []
    readme = _find_readme(path)
    if not readme:
        return DetectorResult(
            score=0, evidence=[], rationale="No README found to check access conditions."
        )

    text = _read_text_safe(readme)
    access_keywords = [
        r"\baccess\b",
        r"\bavailability\b",
        r"\brestrict",
        r"\blicense\b",
        r"\bembargo\b",
        r"\bopen\b",
        r"\bpublic\b",
        r"\bprivate\b",
        r"\bconfidential\b",
    ]
    matched = [kw for kw in access_keywords if re.search(kw, text, re.I)]
    unique_matched = list(dict.fromkeys(matched))  # deduplicate, preserve order

    if len(unique_matched) >= 3:
        score = 2
        rationale = "Multiple access condition indicators found."
    elif unique_matched:
        score = 1
        rationale = "Some access-related terms found but coverage is limited."
    else:
        score = 0
        rationale = "No access condition keywords found in README."

    for kw in unique_matched[:5]:
        evidence.append(f"Keyword match: {kw}")

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_I1(path: Path, mode: str) -> DetectorResult:
    """Structured formal metadata format — same heuristic as F4 but I-principle."""
    # Reuse F4 logic; I1 overlaps but is framed from interoperability perspective
    result = detect_FAIRR_F4(path, mode)
    return DetectorResult(
        score=result.score,
        evidence=result.evidence,
        rationale=result.rationale.replace("Machine-readable", "Interoperable structured"),
    )


def detect_FAIRR_I3(path: Path, mode: str) -> DetectorResult:
    """Links between research system components (data↔code↔env↔outputs)."""
    evidence: List[str] = []
    signals = 0

    # 1. Input dataset references
    if _glob_exists(path, "**/input*") or _glob_exists(path, "**/data/**"):
        evidence.append("input/data directory or file detected")
        signals += 1

    # 2. Container definition
    container_files = _files_matching(path, r"Dockerfile", r"[Ss]ingularity", r"[Aa]pptainer")
    if container_files:
        evidence.append(f"Container: {container_files[0].name}")
        signals += 1

    # 3. Config/parameter files
    config_files = _files_matching(
        path, r"config\.ya?ml$", r"params\.ya?ml$", r"parameters\.ya?ml$",
        r"config\.json$", r"settings\.ya?ml$"
    )
    if config_files:
        evidence.append(f"Config: {config_files[0].name}")
        signals += 1

    # 4. Output directory
    if _glob_exists(path, "**/output*") or _glob_exists(path, "**/results/**"):
        evidence.append("output/results directory detected")
        signals += 1

    # 5. Commit/version reference in README or metadata
    readme = _find_readme(path)
    if readme:
        text = _read_text_safe(readme)
        if re.search(r"\bcommit\b|\bsha\b|github\.com|version\s+[0-9]", text, re.I):
            evidence.append("version/commit reference in README")
            signals += 1

    # 6. Workflow file
    workflow_files = list(path.glob(".github/workflows/*.yml")) + list(
        path.glob("Snakefile")
    ) + list(path.glob("*.nf")) + list(path.glob("*.wdl"))
    if workflow_files:
        evidence.append(f"Workflow: {workflow_files[0].name}")
        signals += 1

    if signals >= 4:
        score = 2
        rationale = f"Strong component linking ({signals} signals)."
    elif signals >= 2:
        score = 1
        rationale = f"Partial component linking ({signals} signals)."
    else:
        score = 0
        rationale = "Insufficient component linking signals."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_R1_1(path: Path, mode: str) -> DetectorResult:
    """License specified (LICENSE file or SPDX identifier)."""
    evidence: List[str] = []

    license_files = _files_matching(
        path, r"^LICENSE(\.md|\.txt)?$", r"^LICENCE(\.md|\.txt)?$", r"^COPYING$"
    )
    for f in license_files:
        evidence.append(f"License file: {f.name}")

    # SPDX in README or CITATION.cff
    spdx_found = False
    for candidate in [_find_readme(path), path / "CITATION.cff"]:
        if candidate and candidate.exists():
            text = _read_text_safe(candidate)
            if re.search(r"SPDX-License-Identifier|spdx\.org/licenses", text, re.I):
                evidence.append(f"SPDX identifier in {candidate.name}")
                spdx_found = True
                break

    if license_files and spdx_found:
        score = 2
        rationale = "LICENSE file and SPDX identifier both present."
    elif license_files or spdx_found:
        score = 1
        rationale = "License file or SPDX identifier detected (not both)."
    else:
        score = 0
        rationale = "No LICENSE file or SPDX identifier found."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_R1_2(path: Path, mode: str) -> DetectorResult:
    """Provenance: commit refs, config snapshot, execution logs."""
    evidence: List[str] = []
    signals = 0

    # Commit hash / .git
    if (path / ".git").exists():
        evidence.append(".git repository present")
        signals += 2  # strong signal

    # Config snapshot
    config_files = _files_matching(
        path,
        r"config.*\.ya?ml$", r"params.*\.ya?ml$", r"parameters.*\.json$",
        r"config.*\.json$", r"settings.*\.ya?ml$",
    )
    if config_files:
        evidence.append(f"Config snapshot: {config_files[0].name}")
        signals += 1

    # Log files
    log_files = list(path.glob("**/*.log"))[:3]
    if log_files:
        evidence.append(f"Log file: {log_files[0].name}")
        signals += 1

    # CITATION or codemeta with version
    for meta_file in [path / "CITATION.cff", path / "codemeta.json"]:
        if meta_file.exists():
            text = _read_text_safe(meta_file)
            if re.search(r"version", text, re.I):
                evidence.append(f"Version in {meta_file.name}")
                signals += 1
                break

    # README mentions provenance keywords
    readme = _find_readme(path)
    if readme:
        text = _read_text_safe(readme)
        if re.search(r"\bprovenance\b|\bcommit\b|\brun id\b|\bexperiment\b", text, re.I):
            evidence.append("Provenance keyword in README")
            signals += 1

    if signals >= 4:
        score = 2
        rationale = f"Strong provenance ({signals} signals detected)."
    elif signals >= 2:
        score = 1
        rationale = f"Partial provenance documentation ({signals} signals)."
    else:
        score = 0
        rationale = "Insufficient provenance signals found."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_R2_1(path: Path, mode: str) -> DetectorResult:
    """Environment captured via lockfile or container definition."""
    evidence: List[str] = []

    env_files = _files_matching(
        path,
        r"^requirements([-_].+)?\.txt$",
        r"^uv\.lock$",
        r"^poetry\.lock$",
        r"^Pipfile\.lock$",
        r"^renv\.lock$",
        r"^environment\.ya?ml$",
        r"^conda.*\.ya?ml$",
        r"^Dockerfile$",
        r"[Aa]pptainer",
        r"[Ss]ingularity",
        r"^setup\.cfg$",
        r"^pyproject\.toml$",
    )
    for f in env_files[:6]:
        evidence.append(f"Environment file: {f.name}")

    container_files = [
        f for f in env_files if re.search(r"Dockerfile|[Ss]ingularity|[Aa]pptainer", f.name)
    ]
    lockfiles = [
        f for f in env_files if re.search(r"\.lock$|requirements.*\.txt", f.name, re.I)
    ]

    if container_files and lockfiles:
        score = 2
        rationale = "Both container definition and lockfile found."
    elif container_files or lockfiles:
        score = 2
        rationale = f"{'Container' if container_files else 'Lockfile'} found."
    elif env_files:
        score = 1
        rationale = "Partial environment specification (pyproject.toml or similar)."
    else:
        score = 0
        rationale = "No environment specification file found."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_R2_4(path: Path, mode: str) -> DetectorResult:
    """Integrity: checksums or sha256 references."""
    evidence: List[str] = []

    checksum_files = _files_matching(
        path,
        r"SHA256SUMS",
        r"checksums?\.txt",
        r"checksums?\.sha256",
        r"md5sums?",
        r"\.sha256$",
    )
    for f in checksum_files:
        evidence.append(f"Checksum file: {f.name}")

    # sha256 mentions in README
    sha_in_readme = False
    readme = _find_readme(path)
    if readme:
        text = _read_text_safe(readme)
        if re.search(r"sha256|sha-256|checksum|hash", text, re.I):
            evidence.append("sha256/checksum mention in README")
            sha_in_readme = True

    # pyproject/metadata with integrity info
    for f in [path / "pyproject.toml", path / "codemeta.json"]:
        if f.exists():
            text = _read_text_safe(f)
            if re.search(r"sha256|checksum", text, re.I):
                evidence.append(f"Integrity reference in {f.name}")
                sha_in_readme = True
                break

    if checksum_files and sha_in_readme:
        score = 2
        rationale = "Checksum file and documentation of integrity present."
    elif checksum_files or sha_in_readme:
        score = 1
        rationale = "Partial integrity verification (file or mention only)."
    else:
        score = 0
        rationale = "No integrity verification files or mentions found."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


def detect_FAIRR_R2_5(path: Path, mode: str) -> DetectorResult:
    """Regeneration instructions: Reproduce/How-to-run in README or script."""
    evidence: List[str] = []
    signals = 0

    readme = _find_readme(path)
    if readme:
        text = _read_text_safe(readme)
        repro_section = re.search(
            r"(##\s*(reproduc|how.to.run|getting.started|quickstart|usage|run|install))",
            text,
            re.I,
        )
        if repro_section:
            evidence.append(
                f"Section '{repro_section.group(0).strip()}' in {readme.name}"
            )
            signals += 2

        if re.search(r"```.*?```", text, re.DOTALL):
            evidence.append("Code blocks in README")
            signals += 1

    # Makefile / run scripts
    run_scripts = _files_matching(
        path,
        r"^Makefile$", r"^run\.sh$", r"^run\.py$", r"^main\.py$",
        r"^workflow\.(sh|py)$", r"^entrypoint\.(sh|py)$",
    )
    for f in run_scripts[:3]:
        evidence.append(f"Run script: {f.name}")
        signals += 1

    if signals >= 3:
        score = 2
        rationale = "Comprehensive regeneration instructions found."
    elif signals >= 1:
        score = 1
        rationale = "Some execution guidance found."
    else:
        score = 0
        rationale = "No regeneration instructions detected."

    return DetectorResult(score=score, evidence=evidence, rationale=rationale)


# ---------------------------------------------------------------------------
# Dispatch table — maps metric ID → detector function
# ---------------------------------------------------------------------------

DETECTOR_MAP = {
    "FAIRR-F2":   detect_FAIRR_F2,
    "FAIRR-F4":   detect_FAIRR_F4,
    "FAIRR-A1":   detect_FAIRR_A1,
    "FAIRR-I1":   detect_FAIRR_I1,
    "FAIRR-I3":   detect_FAIRR_I3,
    "FAIRR-R1.1": detect_FAIRR_R1_1,
    "FAIRR-R1.2": detect_FAIRR_R1_2,
    "FAIRR-R2.1": detect_FAIRR_R2_1,
    "FAIRR-R2.4": detect_FAIRR_R2_4,
    "FAIRR-R2.5": detect_FAIRR_R2_5,
}
