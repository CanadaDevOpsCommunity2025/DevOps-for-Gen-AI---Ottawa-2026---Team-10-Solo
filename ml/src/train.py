"""Train V1 and V2 fraud models."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.config import (
    ARTIFACT_DIR,
    FEATURE_COLUMNS,
    METRICS_PATHS,
    MODEL_PATHS,
    RANDOM_SEED,
    TARGET_COLUMN,
)
from ml.src.evaluate import evaluate_model


def _build_pipeline(random_seed: int) -> Pipeline:
    categorical_features = ["merchant_category", "country"]
    numeric_features = [c for c in FEATURE_COLUMNS if c not in categorical_features]

    preprocess = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_features),
            ("num", "passthrough", numeric_features),
        ]
    )

    clf = RandomForestClassifier(
        n_estimators=250,
        max_depth=12,
        min_samples_leaf=2,
        random_state=random_seed,
        n_jobs=-1,
        class_weight="balanced_subsample",
    )

    return Pipeline(steps=[("preprocess", preprocess), ("model", clf)])


def train_phase1_models() -> dict[str, dict]:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    train_df = pd.read_csv("ml/data/generated/train.csv")
    eval_df = pd.read_csv("ml/data/generated/evaluation.csv")

    X_train = train_df[FEATURE_COLUMNS]
    y_train = train_df[TARGET_COLUMN]

    X_eval = eval_df[FEATURE_COLUMNS]
    y_eval = eval_df[TARGET_COLUMN]

    # Healthy baseline
    v1 = _build_pipeline(RANDOM_SEED)
    v1.fit(X_train, y_train)

    # Controlled regression V2:
    # introduce training bias where high-value legitimate txns are underrepresented,
    # causing higher false positives for that segment in evaluation/live traffic.
    high_value_legit = (train_df[TARGET_COLUMN] == 0) & (train_df["amount"] > 1200)
    keep_mask = ~high_value_legit
    keep_mask.loc[high_value_legit] = (
        train_df.loc[high_value_legit, "amount_vs_customer_average"] < 3.0
    ) | (train_df.loc[high_value_legit].index % 4 == 0)

    biased_train = train_df[keep_mask].copy()
    X_train_v2 = biased_train[FEATURE_COLUMNS]
    y_train_v2 = biased_train[TARGET_COLUMN]

    v2 = _build_pipeline(RANDOM_SEED + 1)
    v2.fit(X_train_v2, y_train_v2)

    joblib.dump(v1, MODEL_PATHS["v1"])
    joblib.dump(v2, MODEL_PATHS["v2"])

    metrics_v1 = evaluate_model(v1, X_eval, y_eval, threshold=0.50)
    metrics_v2 = evaluate_model(v2, X_eval, y_eval, threshold=0.50)

    metrics_v1.update(
        {
            "model_version": "v1",
            "trained_at": datetime.now(UTC).isoformat(),
            "train_rows": int(len(train_df)),
        }
    )
    metrics_v2.update(
        {
            "model_version": "v2",
            "trained_at": datetime.now(UTC).isoformat(),
            "train_rows": int(len(biased_train)),
        }
    )

    with open(METRICS_PATHS["v1"], "w", encoding="utf-8") as f:
        json.dump(metrics_v1, f, indent=2)

    with open(METRICS_PATHS["v2"], "w", encoding="utf-8") as f:
        json.dump(metrics_v2, f, indent=2)

    return {"v1": metrics_v1, "v2": metrics_v2}


if __name__ == "__main__":
    results = train_phase1_models()
    for key in ["v1", "v2"]:
        r = results[key]
        print(
            f"{key.upper()} | acc={r['accuracy']:.4f} precision={r['precision']:.4f} "
            f"recall={r['recall']:.4f} fpr={r['false_positive_rate']:.4f}"
        )
