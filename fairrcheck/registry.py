"""
registry.py — Load and expose the FAIRR metric registry from YAML.

The YAML file is the single source of truth.  Nothing here duplicates metric
definitions; this module only parses and exposes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml


# ---------------------------------------------------------------------------
# Default registry path — resolves relative to this file's package root.
# ---------------------------------------------------------------------------
_DEFAULT_REGISTRY = Path(__file__).parent.parent / "config" / "metrics_fairr_v1.yml"


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------


@dataclass
class MetricSpec:
    """Parsed representation of a single metric entry in the YAML."""

    id: str
    principle: str
    name: str
    description: str
    implemented_in_prototype: bool


@dataclass
class Registry:
    """Full FAIRR registry loaded from YAML."""

    schema_version: str
    name: str
    description: str
    scale: List[int]
    weights: Dict[str, float]
    metrics: List[MetricSpec] = field(default_factory=list)

    # Derived look-ups (populated post-init)
    _by_id: Dict[str, MetricSpec] = field(default_factory=dict, repr=False)
    _by_principle: Dict[str, List[MetricSpec]] = field(
        default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        self._by_id = {m.id: m for m in self.metrics}
        self._by_principle = {}
        for m in self.metrics:
            self._by_principle.setdefault(m.principle, []).append(m)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def get(self, metric_id: str) -> Optional[MetricSpec]:
        return self._by_id.get(metric_id)

    def by_principle(self, principle: str) -> List[MetricSpec]:
        return self._by_principle.get(principle, [])

    @property
    def principles(self) -> List[str]:
        # Preserve definition order
        seen: Dict[str, None] = {}
        for m in self.metrics:
            seen.setdefault(m.principle, None)
        return list(seen)

    @property
    def implemented_metrics(self) -> List[MetricSpec]:
        return [m for m in self.metrics if m.implemented_in_prototype]

    @property
    def max_score(self) -> int:
        return max(self.scale)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_registry(path: Optional[Path] = None) -> Registry:
    """
    Load the FAIRR registry from *path* (defaults to the bundled YAML).

    Raises:
        FileNotFoundError: if the YAML file cannot be found.
        ValueError: if the YAML structure is unexpected.
    """
    registry_path = Path(path) if path else _DEFAULT_REGISTRY

    if not registry_path.exists():
        raise FileNotFoundError(
            f"FAIRR registry YAML not found: {registry_path}\n"
            "Set FAIRRCHECK_REGISTRY env var or pass --registry."
        )

    with registry_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ValueError("Registry YAML must be a mapping at the top level.")

    scoring = raw.get("scoring", {})
    metrics = [
        MetricSpec(
            id=m["id"],
            principle=m["principle"],
            name=m["name"],
            description=m.get("description", "").strip(),
            implemented_in_prototype=bool(m.get("implemented_in_prototype", False)),
        )
        for m in raw.get("metrics", [])
    ]

    return Registry(
        schema_version=raw.get("schema_version", "unknown"),
        name=raw.get("name", "FAIRR"),
        description=raw.get("description", "").strip(),
        scale=scoring.get("scale", [0, 1, 2]),
        weights=scoring.get("weights", {}),
        metrics=metrics,
    )
