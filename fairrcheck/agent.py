"""
agent.py — Aider-style fix agent for fairrcheck.

Implements the safe patch workflow:
  1. Generate unified diff via LLM (or subprocess Aider).
  2. Show the diff to the user.
  3. If --apply and user confirms, apply the patch.

Allowed files (can be extended via config):
    README.md, CITATION.cff, metadata.json
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

# Files the fix agent is allowed to modify
ALLOWED_FILES = {"README.md", "CITATION.cff", "metadata.json"}


# ---------------------------------------------------------------------------
# Aider subprocess helper
# ---------------------------------------------------------------------------


def _aider_available() -> bool:
    try:
        result = subprocess.run(
            ["aider", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
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
        "--message", message,
    ]
    if dry_run:
        cmd.append("--dry-run")
    for f in files:
        cmd.extend(["--file", str(f)])

    env = os.environ.copy()
    # Ensure Aider uses the configured LLM if set
    if os.environ.get("FAIRRCHECK_LLM_BASE_URL"):
        env.setdefault("OPENAI_API_BASE", os.environ["FAIRRCHECK_LLM_BASE_URL"])
    if os.environ.get("FAIRRCHECK_LLM_API_KEY"):
        env.setdefault("OPENAI_API_KEY", os.environ["FAIRRCHECK_LLM_API_KEY"])
    if os.environ.get("FAIRRCHECK_LLM_MODEL"):
        env.setdefault("AIDER_MODEL", os.environ["FAIRRCHECK_LLM_MODEL"])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_path),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        if result.returncode != 0:
            logger.warning("Aider exited with code %d: %s", result.returncode, result.stderr[:500])
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.error("Aider timed out after 180s")
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
    Checks that only allowed files are modified.
    """
    problems = []
    for line in diff.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            # Extract filename from diff header
            parts = line[4:].split("\t")[0].strip()
            # Remove leading a/ b/ prefixes
            for prefix in ("a/", "b/", "./"):
                if parts.startswith(prefix):
                    parts = parts[len(prefix):]
            fname = Path(parts).name
            if fname not in allowed_files and fname not in ("/dev/null", "dev/null"):
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
    Aider-style fix agent.

    Preferred strategy: subprocess Aider (if available).
    Fallback: LLM-generated unified diff.
    """

    def __init__(
        self,
        project_path: Path,
        llm_config: LLMConfig,
        allowed_files: Optional[set] = None,
    ) -> None:
        self.project_path = project_path
        self.llm_config = llm_config
        self.allowed_files = allowed_files or ALLOWED_FILES

    def _collect_target_files(self) -> List[Path]:
        return [
            self.project_path / f
            for f in self.allowed_files
            if (self.project_path / f).exists()
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
            target_files = self._collect_target_files()

            diff = ""
            method = "none"

            if _aider_available():
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
                # Pick the first allowed file as target
                for fname in ["README.md", "CITATION.cff", "metadata.json"]:
                    target = self.project_path / fname
                    if target.exists():
                        content = target.read_text(encoding="utf-8", errors="replace")
                        diff = llm_generate_patch(
                            self.llm_config, metric_id, message, content, fname
                        )
                        method = "llm_patch"
                        break

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
