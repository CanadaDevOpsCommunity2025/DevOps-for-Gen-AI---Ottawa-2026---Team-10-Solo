# DevOps-for-GenAI---Ottawa-2026---Team-10-ThaoPhan
Team 10
Project Name - ChaosGate AI — AI Production Readiness & Chaos Testing Platform
Project Lead: Thao Phan
Other Participants: Anosh Rabbani, Hiba Souihel
Project Title: ChaosGate AI — AI Production Readiness & Chaos Testing Platform
Team Name: N/A

## Person 2: model safety monitoring

`model_monitor` continuously evaluates a rolling window of fraud predictions. It
calculates accuracy, precision, recall, false-positive rate, prediction drift
(PSI), a weighted health score, and a threshold-based status. Person 3 can use
the JSON field `rollback_recommended` to stop V2 traffic.

Run the demo and tests from the repository root:

```powershell
python -m examples.demo_monitor
python -m unittest discover -s tests -v
```

Integration example:

```python
from model_monitor import ModelMonitor

monitor = ModelMonitor(baseline_probabilities=[0.05, 0.92], window_size=500)
report = monitor.add_result(actual=True, predicted=True, probability=0.91)

if report["rollback_recommended"]:
    trigger_person_3_rollback()
```

The initial `INSUFFICIENT_DATA` status prevents deployment decisions before 20
labelled outcomes have arrived. All limits can be changed through
`FailureThresholds`; use a representative V1/validation probability sample as
the drift baseline. PSI measures prediction-score drift here. Feature drift can
be added once Person 1 exposes the production input schema.
