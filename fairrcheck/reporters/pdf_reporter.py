"""pdf_reporter.py — Produce a concise PDF summary via ReportLab."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def write_pdf(
    result: Dict[str, Any],
    out_dir: Path,
    filename: str = "report.pdf",
) -> Path:
    """
    Generate a concise FAIRR PDF summary and write it to *out_dir/filename*.
    Returns the output path.

    Requires: reportlab
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        )
    except ImportError as exc:
        raise ImportError(
            "reportlab is required to generate PDF reports. "
            "Install with: pip install reportlab"
        ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=f"FAIRR Report — {result['project_name']}",
    )

    styles = getSampleStyleSheet()
    story: List[Any] = []

    # ---- Colour helpers ----
    BLUE = colors.HexColor("#0066cc")
    GREEN = colors.HexColor("#059669")
    AMBER = colors.HexColor("#d97706")
    RED = colors.HexColor("#dc2626")
    LIGHT_GRAY = colors.HexColor("#f5f7fa")
    MID_GRAY = colors.HexColor("#6b7280")

    def _score_colour(score: float) -> Any:
        if score >= 0.70:
            return GREEN
        if score >= 0.40:
            return AMBER
        return RED

    # ---- Title ----
    title_style = ParagraphStyle(
        "Title", parent=styles["Title"],
        fontSize=20, textColor=BLUE, spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "Sub", parent=styles["Normal"],
        fontSize=9, textColor=MID_GRAY, spaceAfter=2,
    )
    story.append(Paragraph("FAIRR Compliance Report", title_style))
    story.append(Paragraph(
        f"{result['registry_name']} &nbsp;·&nbsp; "
        f"Mode: {result['scan_mode'].title()} &nbsp;·&nbsp; "
        f"Scanned: {result['scanned_at'][:10]}",
        sub_style,
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=8))

    # ---- Overall score ----
    overall = result["overall_fairr_score"]
    grade = result["grade"]
    score_style = ParagraphStyle(
        "Score", parent=styles["Normal"],
        fontSize=30, textColor=_score_colour(overall),
        spaceAfter=2, fontName="Helvetica-Bold",
    )
    story.append(Paragraph(
        f"{overall * 100:.1f}% &nbsp; Grade {grade}", score_style
    ))
    story.append(Paragraph(
        f"<b>Project:</b> {result['project_name']} &nbsp;·&nbsp; "
        f"<b>Path:</b> {result['project_path']}",
        ParagraphStyle("meta", parent=styles["Normal"], fontSize=8, textColor=MID_GRAY,
                       spaceAfter=10),
    ))

    # ---- Per-principle summary table ----
    story.append(Paragraph("Per-Principle Scores", styles["Heading2"]))
    story.append(Spacer(1, 4))

    principle_names = {
        "F": "Findable", "A": "Accessible", "I": "Interoperable",
        "R": "Reusable", "R2": "Reproducible",
    }

    table_data = [["Principle", "Name", "Weight", "Score", "Implemented"]]
    for p, data in result.get("principles", {}).items():
        norm = data.get("normalised_score", 0)
        weight = data.get("weight", 0)
        impl = data.get("implemented_count", 0)
        total = data.get("total_metrics", 0)
        table_data.append([
            p,
            principle_names.get(p, p),
            f"{weight * 100:.0f}%",
            f"{norm * 100:.1f}%",
            f"{impl}/{total}",
        ])

    p_table = Table(table_data, colWidths=[1.5 * cm, 4 * cm, 2 * cm, 2.5 * cm, 2 * cm])
    p_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [LIGHT_GRAY, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dce1e9")),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(p_table)
    story.append(Spacer(1, 16))

    # ---- Metric detail table ----
    story.append(Paragraph("Metric Details", styles["Heading2"]))
    story.append(Spacer(1, 4))

    mheader = ["ID", "Name", "Principle", "Score", "Notes"]
    m_data = [mheader]
    for m in result.get("metrics", []):
        score_str = (
            f"{m['score']}/{m['max_score']}"
            if m.get("score") is not None
            else "N/A"
        )
        notes = (m.get("notes") or m.get("rationale") or "")[:80]
        m_data.append([
            m["metric_id"],
            (m["name"] or "")[:40],
            m["principle"],
            score_str,
            notes,
        ])

    m_table = Table(
        m_data,
        colWidths=[2.2 * cm, 4.8 * cm, 1.5 * cm, 1.4 * cm, None],
    )
    m_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [LIGHT_GRAY, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.2, colors.HexColor("#dce1e9")),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("WORDWRAP", (4, 1), (4, -1), True),
    ]))
    story.append(m_table)
    story.append(Spacer(1, 16))

    # ---- Footer note ----
    story.append(HRFlowable(width="100%", thickness=0.5, color=MID_GRAY))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"Generated by fairrcheck &nbsp;·&nbsp; {result['registry_name']} v{result['schema_version']}",
        ParagraphStyle("footer", parent=styles["Normal"], fontSize=7, textColor=MID_GRAY),
    ))

    doc.build(story)
    return out_path
