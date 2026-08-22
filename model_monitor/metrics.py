"""Reusable binary-classification metric functions."""

from __future__ import annotations

from collections.abc import Iterable


def _paired_labels(
    actual: Iterable[bool | int], predicted: Iterable[bool | int]
) -> tuple[list[bool], list[bool]]:
    actual_values = [bool(value) for value in actual]
    predicted_values = [bool(value) for value in predicted]
    if len(actual_values) != len(predicted_values):
        raise ValueError("actual and predicted must have the same number of labels")
    if not actual_values:
        raise ValueError("at least one prediction is required")
    return actual_values, predicted_values


def calculate_accuracy(
    actual: Iterable[bool | int], predicted: Iterable[bool | int]
) -> float:
    """Return the fraction of predictions that match their observed labels."""

    actual_values, predicted_values = _paired_labels(actual, predicted)
    correct = sum(a == p for a, p in zip(actual_values, predicted_values))
    return correct / len(actual_values)
