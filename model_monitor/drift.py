"""Population Stability Index (PSI) for numeric transaction features."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import log


def calculate_psi(training: Sequence[float], current: Sequence[float], bins: int = 10) -> float:
    """Compare two numeric distributions; below .10 is typically considered stable."""

    if not training or not current:
        raise ValueError("training and current samples cannot be empty")
    if bins < 2:
        raise ValueError("bins must be at least 2")
    low, high = min(training), max(training)
    if low == high:
        return 0.0 if all(value == low for value in current) else 1.0
    width = (high - low) / bins

    def distribution(values: Sequence[float]) -> list[float]:
        counts = [0.5] * bins  # smoothing avoids log(0)
        for value in values:
            index = min(max(int((value - low) / width), 0), bins - 1)
            counts[index] += 1
        total = sum(counts)
        return [count / total for count in counts]

    expected, observed = distribution(training), distribution(current)
    return sum((o - e) * log(o / e) for e, o in zip(expected, observed))


def calculate_feature_drift(
    training_data: Mapping[str, Sequence[float]],
    current_data: Mapping[str, Sequence[float]],
) -> dict[str, object]:
    """Return per-feature PSI and their average as the overall drift score."""

    if set(training_data) != set(current_data):
        raise ValueError("training and current data must contain the same features")
    if not training_data:
        raise ValueError("at least one feature is required")
    features = {
        name: calculate_psi(training_data[name], current_data[name])
        for name in training_data
    }
    return {"score": sum(features.values()) / len(features), "features": features}
