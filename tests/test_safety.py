import unittest

from model_monitor.safety import evaluate_model_health


class FailureThresholdTests(unittest.TestCase):
    def test_healthy_model_can_continue(self):
        result = evaluate_model_health(
            {"accuracy": 0.97, "recall": 0.96, "false_positive_rate": 0.02},
            0.02,
            96,
        )
        self.assertEqual(result["status"], "HEALTHY")
        self.assertFalse(result["rollback_required"])

    def test_hard_recall_failure_requires_rollback_despite_score(self):
        result = evaluate_model_health(
            {"accuracy": 0.95, "recall": 0.50, "false_positive_rate": 0.02},
            0.02,
            90,
        )
        self.assertEqual(result["status"], "CRITICAL")
        self.assertTrue(result["rollback_required"])
        self.assertIn("Recall below critical threshold", result["failed_checks"])

    def test_warning_drift_generates_event(self):
        result = evaluate_model_health(
            {"accuracy": 0.95, "recall": 0.90, "false_positive_rate": 0.03},
            0.15,
            88,
        )
        self.assertEqual(result["status"], "WARNING")
        self.assertIn("Data drift detected", [event["message"] for event in result["events"]])

    def test_low_health_score_is_critical(self):
        result = evaluate_model_health(
            {"accuracy": 0.90, "recall": 0.90, "false_positive_rate": 0.02},
            0.02,
            60,
        )
        self.assertEqual(result["status"], "CRITICAL")


if __name__ == "__main__":
    unittest.main()
