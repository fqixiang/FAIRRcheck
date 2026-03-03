"""
scoring.py — Aggregate metric scores into per-principle and overall FAIRR score.

Formulae
--------
Per-principle normalised score:
    P_score = mean(score_i / max_score) for metrics i in principle P

Overall FAIRR score (weighted):
    FAIRR = sum(weight_P * P_score) for P in {F, A, I, R, R2}

All scores are in [0, 1].
"""

from __future__ import annotations

from typing import Any, Dict, List

from .registry import Registry


def compute_scores(
    metric_results: List[Dict[str, Any]],
    registry: Registry,
) -> Dict[str, Any]:
    """
    Compute per-principle averages and overall weighted FAIRR score.

    Parameters
    ----------
    metric_results:
        List of metric result dicts (as produced by the scanner).
    registry:
        Loaded FAIRR registry (provides weights and principles).

    Returns
    -------
    Dict with keys: 'principles', 'overall_fairr_score', 'score_summary'.
    """
    max_s = registry.max_score

    # ----- per-principle accumulator -----
    principle_data: Dict[str, Dict[str, Any]] = {}
    for p in registry.principles:
        principle_data[p] = {
            "scores": [],
            "metrics": [],
            "weight": registry.weights.get(p, 0.0),
        }

    for r in metric_results:
        p = r["principle"]
        if p not in principle_data:
            principle_data[p] = {"scores": [], "metrics": [], "weight": 0.0}

        principle_data[p]["metrics"].append(r["metric_id"])

        # Only count implemented metrics with actual scores
        if r.get("implemented") and r.get("score") is not None:
            principle_data[p]["scores"].append(r["score"] / max_s)

    # ----- per-principle averages -----
    principle_summary: Dict[str, Any] = {}
    for p, data in principle_data.items():
        scores = data["scores"]
        avg = sum(scores) / len(scores) if scores else 0.0
        principle_summary[p] = {
            "normalised_score": round(avg, 4),
            "implemented_count": len(scores),
            "total_metrics": len(data["metrics"]),
            "weight": data["weight"],
        }

    # ----- overall weighted score -----
    total_weight = sum(
        principle_summary[p]["weight"]
        for p in principle_summary
        if principle_summary[p]["weight"] > 0
    )
    if total_weight == 0:
        overall = 0.0
    else:
        weighted_sum = sum(
            principle_summary[p]["weight"] * principle_summary[p]["normalised_score"]
            for p in principle_summary
        )
        overall = weighted_sum / total_weight

    # ----- letter-grade helper -----
    def _grade(score: float) -> str:
        if score >= 0.85:
            return "A"
        if score >= 0.70:
            return "B"
        if score >= 0.50:
            return "C"
        if score >= 0.30:
            return "D"
        return "F"

    return {
        "principles": principle_summary,
        "overall_fairr_score": round(overall, 4),
        "grade": _grade(overall),
        "score_summary": {
            p: round(principle_summary[p]["normalised_score"], 4)
            for p in principle_summary
        },
    }
