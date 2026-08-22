"""Rolling classification metrics, prediction drift, and model health decisions."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from math import log
from typing import Iterable


@dataclass(frozen=True)
class ModelResult:
    actual: bool
    predicted: bool
    probability: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be between 0 and 1")


@dataclass(frozen=True)
class FailureThresholds:
    """WARNING limits; CRITICAL limits represent an unsafe model."""

    min_samples: int = 20
    warning_accuracy: float = 0.90
    critical_accuracy: float = 0.80
    warning_precision: float = 0.85
    critical_precision: float = 0.70
    warning_recall: float = 0.85
    critical_recall: float = 0.70
    warning_false_positive_rate: float = 0.10
    critical_false_positive_rate: float = 0.20
    warning_drift: float = 0.10
    critical_drift: float = 0.25
    warning_health_score: int = 75
    critical_health_score: int = 50


class ModelMonitor:
    """Monitor a bounded rolling window of binary fraud predictions.

    Drift is PSI across ten probability buckets compared with a baseline.
    A lower health score is worse. Any critical failure makes rollback advisable.
    """

    def __init__(
        self,
        baseline_probabilities: Iterable[float] | None = None,
        *,
        window_size: int = 500,
        thresholds: FailureThresholds | None = None,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size must be positive")
        self.results: deque[ModelResult] = deque(maxlen=window_size)
        self.thresholds = thresholds or FailureThresholds()
        self._baseline = self._distribution(baseline_probabilities or [])

    def add_result(self, actual: bool, predicted: bool, probability: float) -> dict:
        self.results.append(ModelResult(bool(actual), bool(predicted), probability))
        return self.snapshot()

    def add_batch(self, results: Iterable[ModelResult]) -> dict:
        for result in results:
            self.results.append(result)
        return self.snapshot()

    def set_baseline(self, probabilities: Iterable[float]) -> None:
        values = list(probabilities)
        if not values:
            raise ValueError("baseline must contain at least one probability")
        self._baseline = self._distribution(values)

    @staticmethod
    def _distribution(values: Iterable[float]) -> list[float] | None:
        values = list(values)
        if not values:
            return None
        counts = [0] * 10
        for value in values:
            if not 0.0 <= value <= 1.0:
                raise ValueError("baseline probabilities must be between 0 and 1")
            counts[min(int(value * 10), 9)] += 1
        total = len(values)
        return [count / total for count in counts]

    def _drift(self) -> float:
        if self._baseline is None or not self.results:
            return 0.0
        current = self._distribution(result.probability for result in self.results)
        assert current is not None
        epsilon = 1e-6
        return sum(
            (max(c, epsilon) - max(b, epsilon))
            * log(max(c, epsilon) / max(b, epsilon))
            for b, c in zip(self._baseline, current)
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @staticmethod
    def _severity(value: float, warning: float, critical: float, *, lower_is_bad: bool) -> str:
        if lower_is_bad:
            return "critical" if value < critical else "warning" if value < warning else "healthy"
        return "critical" if value > critical else "warning" if value > warning else "healthy"

    def snapshot(self) -> dict:
        tp = sum(r.actual and r.predicted for r in self.results)
        tn = sum(not r.actual and not r.predicted for r in self.results)
        fp = sum(not r.actual and r.predicted for r in self.results)
        fn = sum(r.actual and not r.predicted for r in self.results)
        count = len(self.results)
        metrics = {
            "accuracy": self._ratio(tp + tn, count),
            "precision": self._ratio(tp, tp + fp),
            "recall": self._ratio(tp, tp + fn),
            "false_positive_rate": self._ratio(fp, fp + tn),
            "data_drift": self._drift(),
        }
        t = self.thresholds
        checks = {
            "accuracy": self._severity(metrics["accuracy"], t.warning_accuracy, t.critical_accuracy, lower_is_bad=True),
            "precision": self._severity(metrics["precision"], t.warning_precision, t.critical_precision, lower_is_bad=True),
            "recall": self._severity(metrics["recall"], t.warning_recall, t.critical_recall, lower_is_bad=True),
            "false_positive_rate": self._severity(metrics["false_positive_rate"], t.warning_false_positive_rate, t.critical_false_positive_rate, lower_is_bad=False),
            "data_drift": self._severity(metrics["data_drift"], t.warning_drift, t.critical_drift, lower_is_bad=False),
        }
        # Weighted safety score: missed fraud and overall correctness matter most.
        drift_quality = max(0.0, 1.0 - min(metrics["data_drift"] / t.critical_drift, 1.0))
        fpr_quality = 1.0 - metrics["false_positive_rate"]
        score = round(100 * (
            0.30 * metrics["accuracy"] + 0.20 * metrics["precision"]
            + 0.30 * metrics["recall"] + 0.10 * fpr_quality + 0.10 * drift_quality
        )) if count else 0

        if count < t.min_samples:
            status, reason = "INSUFFICIENT_DATA", f"need {t.min_samples - count} more samples"
        elif "critical" in checks.values() or score < t.critical_health_score:
            status, reason = "CRITICAL", "one or more critical thresholds failed"
        elif "warning" in checks.values() or score < t.warning_health_score:
            status, reason = "WARNING", "one or more warning thresholds failed"
        else:
            status, reason = "HEALTHY", "all thresholds passed"

        return {
            "sample_count": count,
            "window_size": self.results.maxlen,
            "metrics": metrics,
            "confusion_matrix": {"true_positive": tp, "true_negative": tn, "false_positive": fp, "false_negative": fn},
            "health_score": score,
            "status": status,
            "reason": reason,
            "rollback_recommended": status == "CRITICAL",
            "threshold_checks": checks,
            "thresholds": asdict(t),
        }

    def format_report(self) -> str:
        report = self.snapshot()
        m = report["metrics"]
        icon = {"HEALTHY": "🟢", "WARNING": "🟡", "CRITICAL": "🔴", "INSUFFICIENT_DATA": "⚪"}[report["status"]]
        return "\n".join((
            "MODEL HEALTH", "─────────────",
            f"Accuracy:          {m['accuracy']:6.1%}",
            f"Precision:         {m['precision']:6.1%}",
            f"Recall:            {m['recall']:6.1%}",
            f"False Positive:    {m['false_positive_rate']:6.1%}",
            f"Data Drift (PSI):  {m['data_drift']:6.1%}", "",
            f"Health Score: {report['health_score']}/100", "",
            f"Status: {icon} {report['status']}",
        ))
