"""Failure thresholds, deployment status, and actionable monitoring events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ThresholdPolicy:
    healthy_score: int = 85
    critical_score: int = 65
    critical_accuracy: float = 0.80
    critical_recall: float = 0.75
    critical_false_positive_rate: float = 0.10
    warning_drift: float = 0.10
    critical_drift: float = 0.25


def evaluate_model_health(
    metrics: Mapping[str, float],
    drift: float,
    health_score: int,
    policy: ThresholdPolicy | None = None,
) -> dict[str, object]:
    """Return status, rollback decision, failed checks, and dashboard events."""

    policy = policy or ThresholdPolicy()
    required = {"accuracy", "recall", "false_positive_rate"}
    missing = required - metrics.keys()
    if missing:
        raise ValueError(f"missing metrics: {', '.join(sorted(missing))}")

    critical_failures = []
    if metrics["accuracy"] < policy.critical_accuracy:
        critical_failures.append("Accuracy below critical threshold")
    if metrics["recall"] < policy.critical_recall:
        critical_failures.append("Recall below critical threshold")
    if metrics["false_positive_rate"] > policy.critical_false_positive_rate:
        critical_failures.append("False-positive rate above critical threshold")
    if drift > policy.critical_drift:
        critical_failures.append("Data drift above critical threshold")

    if critical_failures or health_score < policy.critical_score:
        status = "CRITICAL"
    elif health_score < policy.healthy_score or drift >= policy.warning_drift:
        status = "WARNING"
    else:
        status = "HEALTHY"

    events = [
        {"severity": "CRITICAL", "message": message} for message in critical_failures
    ]
    if policy.warning_drift <= drift <= policy.critical_drift:
        events.append({"severity": "WARNING", "message": "Data drift detected"})
    if status == "CRITICAL":
        events.append({"severity": "CRITICAL", "message": "Rollback recommended"})
    elif status == "HEALTHY":
        events.append({"severity": "INFO", "message": "Model performance within thresholds"})

    return {
        "status": status,
        "rollback_required": status == "CRITICAL",
        "failed_checks": critical_failures,
        "events": events,
    }
