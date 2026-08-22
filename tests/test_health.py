import unittest

from model_monitor.health import calculate_health_score


class HealthScoreTests(unittest.TestCase):
    def test_scores_healthy_model(self):
        score = calculate_health_score(
            {"accuracy": 0.97, "precision": 0.95, "recall": 0.96}, 0.02
        )
        self.assertEqual(score, 96)

    def test_drift_reduces_health(self):
        metrics = {"accuracy": 0.9, "precision": 0.9, "recall": 0.9}
        self.assertGreater(
            calculate_health_score(metrics, 0.0),
            calculate_health_score(metrics, 0.5),
        )

    def test_rejects_missing_metrics(self):
        with self.assertRaises(ValueError):
            calculate_health_score({"accuracy": 1.0}, 0.0)


if __name__ == "__main__":
    unittest.main()
