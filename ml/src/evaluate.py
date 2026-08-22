"""Model evaluation utilities and phase-1 comparison report."""

from __future__ import annotations

import json
from time import perf_counter

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from ml.config import FEATURE_COLUMNS, METRICS_PATHS, TARGET_COLUMN


def evaluate_model(model, X: pd.DataFrame, y: pd.Series, threshold: float = 0.5) -> dict:
    start = perf_counter()
    probabilities = model.predict_proba(X)[:, 1]
    latency_ms = (perf_counter() - start) * 1000.0 / max(len(X), 1)

    preds = (probabilities >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y, preds, labels=[0, 1]).ravel()

    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    fnr = fn / (fn + tp) if (fn + tp) else 0.0

    metrics = {
        "samples": int(len(X)),
        "threshold": threshold,
        "accuracy": float(accuracy_score(y, preds)),
        "precision": float(precision_score(y, preds, zero_division=0)),
        "recall": float(recall_score(y, preds, zero_division=0)),
        "f1": float(f1_score(y, preds, zero_division=0)),
        "false_positive_rate": float(fpr),
        "false_negative_rate": float(fnr),
        "roc_auc": float(roc_auc_score(y, probabilities)),
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
        "avg_inference_latency_ms_per_sample": float(latency_ms),
        "avg_probability": float(np.mean(probabilities)),
    }
    return metrics


def compare_models_phase1() -> dict:
    with open(METRICS_PATHS["v1"], "r", encoding="utf-8") as f:
        v1 = json.load(f)
    with open(METRICS_PATHS["v2"], "r", encoding="utf-8") as f:
        v2 = json.load(f)

    comparison = {
        "accuracy_delta": v2["accuracy"] - v1["accuracy"],
        "precision_delta": v2["precision"] - v1["precision"],
        "recall_delta": v2["recall"] - v1["recall"],
        "f1_delta": v2["f1"] - v1["f1"],
        "false_positive_rate_delta": v2["false_positive_rate"] - v1["false_positive_rate"],
        "false_negative_rate_delta": v2["false_negative_rate"] - v1["false_negative_rate"],
    }

    output = {"v1": v1, "v2": v2, "comparison": comparison}

    with open(METRICS_PATHS["comparison"], "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print("MODEL COMPARISON (PHASE 1)")
    print("=" * 60)
    print(f"Accuracy       V1={v1['accuracy']:.4f}  V2={v2['accuracy']:.4f}  Δ={comparison['accuracy_delta']:+.4f}")
    print(f"Precision      V1={v1['precision']:.4f}  V2={v2['precision']:.4f}  Δ={comparison['precision_delta']:+.4f}")
    print(f"Recall         V1={v1['recall']:.4f}  V2={v2['recall']:.4f}  Δ={comparison['recall_delta']:+.4f}")
    print(f"F1             V1={v1['f1']:.4f}  V2={v2['f1']:.4f}  Δ={comparison['f1_delta']:+.4f}")
    print(f"False Positive V1={v1['false_positive_rate']:.4f}  V2={v2['false_positive_rate']:.4f}  Δ={comparison['false_positive_rate_delta']:+.4f}")
    print(f"False Negative V1={v1['false_negative_rate']:.4f}  V2={v2['false_negative_rate']:.4f}  Δ={comparison['false_negative_rate_delta']:+.4f}")

    return output


if __name__ == "__main__":
    compare_models_phase1()
