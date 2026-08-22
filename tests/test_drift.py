import unittest

from model_monitor.drift import calculate_feature_drift, calculate_psi


class DriftTests(unittest.TestCase):
    def test_similar_distribution_has_low_drift(self):
        training = list(range(20)) * 5
        self.assertLess(calculate_psi(training, training.copy()), 0.10)

    def test_changed_transactions_have_critical_drift(self):
        training = [40, 60, 75, 82, 91, 100, 120] * 10
        current = [800, 900, 1200, 1500, 2000] * 10
        self.assertGreater(calculate_psi(training, current), 0.25)

    def test_combines_multiple_feature_scores(self):
        result = calculate_feature_drift(
            {"amount": list(range(20)), "hour": list(range(20))},
            {"amount": list(range(20)), "hour": [23] * 20},
        )
        self.assertEqual(set(result["features"]), {"amount", "hour"})
        self.assertAlmostEqual(
            result["score"], sum(result["features"].values()) / 2
        )

    def test_requires_matching_features(self):
        with self.assertRaises(ValueError):
            calculate_feature_drift({"amount": [1]}, {"hour": [1]})


if __name__ == "__main__":
    unittest.main()
