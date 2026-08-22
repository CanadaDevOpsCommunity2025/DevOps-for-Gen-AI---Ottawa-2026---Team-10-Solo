"""Generate synthetic banking transaction data for fraud modeling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ml.config import DATA_DIR, EVAL_SIZE, LIVE_SIZE, RANDOM_SEED, TRAIN_SIZE


@dataclass(frozen=True)
class SplitConfig:
    size: int
    split: Literal["train", "evaluation", "live"]


MERCHANT_CATEGORIES = [
    "groceries",
    "electronics",
    "travel",
    "fuel",
    "restaurants",
    "luxury",
    "online_retail",
    "utilities",
]

COUNTRIES = ["US", "CA", "GB", "MX", "FR", "DE", "JP", "BR"]
HOME_COUNTRY = "US"


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def generate_transactions(config: SplitConfig, seed: int = RANDOM_SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = config.size

    # Core behavioral and profile features
    amount = np.round(rng.lognormal(mean=4.2, sigma=1.0, size=n), 2)
    hour = rng.integers(0, 24, size=n)

    merchant_category = rng.choice(
        MERCHANT_CATEGORIES,
        size=n,
        p=[0.22, 0.12, 0.10, 0.14, 0.18, 0.05, 0.14, 0.05],
    )

    country = rng.choice(COUNTRIES, size=n, p=[0.68, 0.14, 0.05, 0.04, 0.03, 0.03, 0.02, 0.01])
    is_international = (country != HOME_COUNTRY).astype(int)

    device_age_days = np.clip(rng.gamma(shape=2.8, scale=120.0, size=n), 0, 3650).round().astype(int)
    account_age_days = np.clip(rng.gamma(shape=4.0, scale=300.0, size=n), 5, 5500).round().astype(int)

    transactions_last_hour = np.clip(rng.poisson(lam=1.4, size=n), 0, 20)
    transactions_last_24h = np.clip(
        transactions_last_hour + rng.poisson(lam=6.5, size=n), 0, 80
    )

    distance_from_home_km = np.clip(rng.gamma(shape=1.8, scale=30.0, size=n), 0, 5000)

    card_present = (rng.random(n) > 0.35).astype(int)
    previous_declines_24h = np.clip(rng.poisson(lam=0.25, size=n), 0, 12)

    avg_transaction_amount_30d = np.clip(rng.lognormal(mean=3.8, sigma=0.7, size=n), 5.0, 5000.0)
    amount_vs_customer_average = np.clip(amount / np.maximum(avg_transaction_amount_30d, 1.0), 0.05, 50.0)

    is_new_merchant = (rng.random(n) < 0.12).astype(int)

    if config.split == "live":
        # Shift live traffic toward high-value legitimate-like transactions
        # to expose V2 false-positive regression in canary phase.
        hv_idx = rng.random(n) < 0.20
        amount[hv_idx] = np.round(np.clip(rng.lognormal(mean=7.2, sigma=0.40, size=hv_idx.sum()), 400, 6000), 2)
        card_present[hv_idx] = 1
        is_new_merchant[hv_idx] = 0
        previous_declines_24h[hv_idx] = 0
        device_age_days[hv_idx] = np.maximum(device_age_days[hv_idx], 120)
        account_age_days[hv_idx] = np.maximum(account_age_days[hv_idx], 900)
        country[hv_idx] = HOME_COUNTRY
        is_international[hv_idx] = 0
        amount_vs_customer_average[hv_idx] = np.clip(amount[hv_idx] / np.maximum(avg_transaction_amount_30d[hv_idx], 1.0), 1.2, 6.0)

    # Base fraud logit from multi-signal interaction (no single perfect predictor)
    night = ((hour <= 4) | (hour >= 23)).astype(int)

    high_risk_merchant_weight = np.select(
        [merchant_category == "electronics", merchant_category == "travel", merchant_category == "luxury", merchant_category == "online_retail"],
        [0.35, 0.30, 0.45, 0.25],
        default=0.0,
    )

    logit = (
        -4.8
        + 0.50 * np.log1p(amount)
        + 0.95 * (amount_vs_customer_average > 4.0).astype(int)
        + 0.55 * (device_age_days < 14).astype(int)
        + 0.60 * is_international
        + 0.35 * night
        + 0.25 * np.clip(transactions_last_hour - 2, 0, None)
        + 0.08 * np.clip(transactions_last_24h - 10, 0, None)
        + 0.35 * (distance_from_home_km > 120).astype(int)
        + 0.45 * np.clip(previous_declines_24h, 0, 4)
        + 0.45 * is_new_merchant
        + 0.25 * (1 - card_present)
        + high_risk_merchant_weight
        - 0.35 * (account_age_days > 700).astype(int)
    )

    # Interaction boosts risk when multiple suspicious signals co-occur
    interaction = (
        (amount_vs_customer_average > 8.0).astype(int)
        * (is_international == 1).astype(int)
        * (1 - card_present)
    )
    logit += 0.8 * interaction

    # Controlled randomness/noise so task remains realistic
    logit += rng.normal(0, 0.30, size=n)

    fraud_prob = _sigmoid(logit)
    fraud = (rng.random(n) < fraud_prob).astype(int)

    df = pd.DataFrame(
        {
            "transaction_id": [f"tx_{config.split}_{i:08d}" for i in range(n)],
            "customer_id": [f"cust_{x:06d}" for x in rng.integers(0, 12000, size=n)],
            "amount": amount,
            "hour": hour,
            "merchant_category": merchant_category,
            "country": country,
            "is_international": is_international,
            "device_age_days": device_age_days,
            "account_age_days": account_age_days,
            "transactions_last_hour": transactions_last_hour,
            "transactions_last_24h": transactions_last_24h,
            "distance_from_home_km": np.round(distance_from_home_km, 2),
            "card_present": card_present,
            "previous_declines_24h": previous_declines_24h,
            "avg_transaction_amount_30d": np.round(avg_transaction_amount_30d, 2),
            "amount_vs_customer_average": np.round(amount_vs_customer_average, 3),
            "is_new_merchant": is_new_merchant,
            "fraud": fraud,
        }
    )

    return df


def generate_all_datasets(seed: int = RANDOM_SEED) -> dict[str, pd.DataFrame]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    splits = [
        SplitConfig(size=TRAIN_SIZE, split="train"),
        SplitConfig(size=EVAL_SIZE, split="evaluation"),
        SplitConfig(size=LIVE_SIZE, split="live"),
    ]

    dataframes: dict[str, pd.DataFrame] = {}
    for idx, split_cfg in enumerate(splits):
        df = generate_transactions(split_cfg, seed=seed + idx)
        path = DATA_DIR / f"{split_cfg.split}.csv"
        df.to_csv(path, index=False)
        dataframes[split_cfg.split] = df

    return dataframes


if __name__ == "__main__":
    generated = generate_all_datasets()
    for name, frame in generated.items():
        print(
            f"{name}: shape={frame.shape}, fraud_ratio={frame['fraud'].mean():.4f}, "
            f"path={DATA_DIR / f'{name}.csv'}"
        )
