"""Overall model-health scoring."""

from __future__ import annotations

from collections.abc import Mapping


def calculate_health_score(metrics: Mapping[str, float], drift: float) -> int:
    """Combine performance and drift into a score from 0 to 100."""

    required = {"accuracy", "precision", "recall"}
    missing = required - metrics.keys()
    if missing:
        raise ValueError(f"missing metrics: {', '.join(sorted(missing))}")
    values = [metrics[name] for name in required]
    if any(not 0.0 <= value <= 1.0 for value in values) or drift < 0.0:
        raise ValueError("metrics must be within 0..1 and drift cannot be negative")
    drift_quality = max(0.0, 1.0 - drift)
    score = (
        metrics["accuracy"] * 0.30
        + metrics["precision"] * 0.25
        + metrics["recall"] * 0.25
        + drift_quality * 0.20
    )
    return round(score * 100)
