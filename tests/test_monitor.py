import unittest

from model_monitor import FailureThresholds, ModelMonitor, ModelResult


class ModelMonitorTests(unittest.TestCase):
    def setUp(self):
        self.thresholds = FailureThresholds(min_samples=10)
        self.baseline = [0.1, 0.9] * 50

    def test_healthy_model(self):
        monitor = ModelMonitor(self.baseline, thresholds=self.thresholds)
        results = [ModelResult(True, True, 0.9)] * 48
        results += [ModelResult(False, False, 0.1)] * 48
        results += [ModelResult(False, True, 0.9), ModelResult(True, False, 0.1)] * 2
        report = monitor.add_batch(results)
        self.assertAlmostEqual(report["metrics"]["accuracy"], 0.96)
        self.assertEqual(report["status"], "HEALTHY")
        self.assertFalse(report["rollback_recommended"])

    def test_bad_model_recommends_rollback(self):
        monitor = ModelMonitor(self.baseline, thresholds=self.thresholds)
        results = [ModelResult(True, True, 0.9)] * 30
        results += [ModelResult(False, False, 0.1)] * 40
        results += [ModelResult(False, True, 0.9)] * 15
        results += [ModelResult(True, False, 0.1)] * 15
        report = monitor.add_batch(results)
        self.assertEqual(report["status"], "CRITICAL")
        self.assertTrue(report["rollback_recommended"])

    def test_requires_enough_samples(self):
        monitor = ModelMonitor(thresholds=self.thresholds)
        report = monitor.add_result(True, True, 0.9)
        self.assertEqual(report["status"], "INSUFFICIENT_DATA")

    def test_rolling_window(self):
        monitor = ModelMonitor(window_size=3, thresholds=FailureThresholds(min_samples=1))
        monitor.add_batch([ModelResult(True, False, 0.1)] * 3)
        report = monitor.add_batch([ModelResult(True, True, 0.9)] * 3)
        self.assertEqual(report["sample_count"], 3)
        self.assertEqual(report["metrics"]["accuracy"], 1.0)

    def test_rejects_invalid_probability(self):
        with self.assertRaises(ValueError):
            ModelResult(True, True, 1.1)


if __name__ == "__main__":
    unittest.main()
