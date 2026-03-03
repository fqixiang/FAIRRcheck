"""html_reporter.py — Render the FAIRR scan result as an HTML page using Jinja2."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def write_html(
    result: Dict[str, Any],
    out_dir: Path,
    filename: str = "report.html",
) -> Path:
    """
    Render *result* into an HTML report and write it to *out_dir/filename*.
    Returns the output path.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    # Make the selectattr filter work with None-safe comparisons
    template = env.get_template("report.html.j2")
    html = template.render(result=result)

    out_path = out_dir / filename
    out_path.write_text(html, encoding="utf-8")
    return out_path
