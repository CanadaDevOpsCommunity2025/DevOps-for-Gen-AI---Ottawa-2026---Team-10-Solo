import unittest

from model_monitor.metrics import (
    calculate_accuracy,
    calculate_false_positive_rate,
    calculate_precision_recall,
)


class AccuracyTests(unittest.TestCase):
    def test_calculates_accuracy(self):
        self.assertAlmostEqual(
            calculate_accuracy([0, 1, 0, 0, 1, 1, 0], [0, 1, 0, 1, 1, 0, 0]),
            5 / 7,
        )

    def test_rejects_different_lengths(self):
        with self.assertRaises(ValueError):
            calculate_accuracy([0, 1], [0])

    def test_rejects_empty_input(self):
        with self.assertRaises(ValueError):
            calculate_accuracy([], [])


class PrecisionRecallTests(unittest.TestCase):
    def test_calculates_precision_and_recall(self):
        result = calculate_precision_recall(
            [0, 1, 0, 0, 1, 1, 0], [0, 1, 0, 1, 1, 0, 0]
        )
        self.assertAlmostEqual(result["precision"], 2 / 3)
        self.assertAlmostEqual(result["recall"], 2 / 3)

    def test_returns_zero_when_no_positive_predictions_or_labels(self):
        self.assertEqual(
            calculate_precision_recall([0, 0], [0, 0]),
            {"precision": 0.0, "recall": 0.0},
        )


class FalsePositiveRateTests(unittest.TestCase):
    def test_calculates_false_positive_rate(self):
        self.assertAlmostEqual(
            calculate_false_positive_rate(
                [0, 1, 0, 0, 1, 1, 0], [0, 1, 0, 1, 1, 0, 0]
            ),
            1 / 4,
        )

    def test_returns_zero_when_there_are_no_legitimate_transactions(self):
        self.assertEqual(calculate_false_positive_rate([1, 1], [1, 0]), 0.0)


if __name__ == "__main__":
    unittest.main()
