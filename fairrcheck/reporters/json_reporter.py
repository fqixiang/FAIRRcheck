"""json_reporter.py — Write the FAIRR scan result as formatted JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def write_json(result: Dict[str, Any], out_dir: Path, filename: str = "report.json") -> Path:
    """
    Serialise *result* to *out_dir/filename* and return the output path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path
