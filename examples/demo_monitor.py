import sys

from model_monitor import FailureThresholds, ModelMonitor, ModelResult


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


baseline = [0.08] * 50 + [0.91] * 50
monitor = ModelMonitor(baseline, thresholds=FailureThresholds(min_samples=20))

# Replace this batch with results arriving from Person 1 / the backend.
monitor.add_batch(
    [ModelResult(True, True, 0.91)] * 48
    + [ModelResult(False, False, 0.08)] * 48
    + [ModelResult(False, True, 0.91)] * 2
    + [ModelResult(True, False, 0.08)] * 2
)

print(monitor.format_report())
