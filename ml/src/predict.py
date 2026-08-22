"""Stable prediction interface for V1/V2."""

from __future__ import annotations

from time import perf_counter

import joblib
import pandas as pd

from ml.config import FEATURE_COLUMNS, MODEL_PATHS, MODEL_THRESHOLD


class PredictionError(ValueError):
    """Raised when prediction input is invalid."""


def _load_model(model_version: str):
    mv = model_version.lower()
    if mv not in MODEL_PATHS:
        raise PredictionError(f"Invalid model_version '{model_version}'. Expected one of {list(MODEL_PATHS.keys())}.")

    model_path = MODEL_PATHS[mv]
    if not model_path.exists():
        raise PredictionError(f"Model artifact not found: {model_path}. Train models first.")

    return joblib.load(model_path), mv


def _validate_transaction(transaction: dict) -> None:
    missing = [c for c in FEATURE_COLUMNS if c not in transaction]
    if missing:
        raise PredictionError(f"Missing required fields: {missing}")


def predict_transaction(model_version: str, transaction: dict) -> dict:
    _validate_transaction(transaction)
    model, normalized_mv = _load_model(model_version)

    input_df = pd.DataFrame([transaction], columns=FEATURE_COLUMNS)

    start = perf_counter()
    proba = float(model.predict_proba(input_df)[0, 1])
    latency_ms = (perf_counter() - start) * 1000.0

    pred = int(proba >= MODEL_THRESHOLD)

    return {
        "model_version": normalized_mv,
        "fraud_probability": proba,
        "prediction": pred,
        "threshold": MODEL_THRESHOLD,
        "latency_ms": latency_ms,
    }


if __name__ == "__main__":
    sample = {
        "amount": 1800.0,
        "hour": 2,
        "merchant_category": "electronics",
        "country": "US",
        "is_international": 0,
        "device_age_days": 2,
        "account_age_days": 950,
        "transactions_last_hour": 5,
        "transactions_last_24h": 12,
        "distance_from_home_km": 340.0,
        "card_present": 0,
        "previous_declines_24h": 2,
        "avg_transaction_amount_30d": 120.0,
        "amount_vs_customer_average": 15.0,
        "is_new_merchant": 1,
    }

    for mv in ("v1", "v2"):
        try:
            print(mv, predict_transaction(mv, sample))
        except PredictionError as exc:
            print(f"{mv} error: {exc}")
