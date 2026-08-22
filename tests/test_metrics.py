import unittest

from model_monitor.metrics import calculate_accuracy


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


if __name__ == "__main__":
    unittest.main()
