"""
Distributions.

Uniform random noise produces a dashboard that looks fake the moment anyone
plots a histogram. Real AI-app telemetry has specific shapes:

  * latency is lognormal with a fat right tail (network + queueing + decode)
  * output tokens are roughly geometric-ish, capped by max_tokens
  * retrieval similarity scores are beta-distributed, bunched high when the
    corpus covers the query and collapsing toward 0.2 when it doesn't
  * traffic volume is diurnal

Everything here takes an explicit `rnd: random.Random` so a --seed reproduces a
run exactly. You want that when a judge says "do the PII one again".
"""
from __future__ import annotations

import math
import random


def lognormal_ms(rnd: random.Random, median_ms: float, sigma: float = 0.45,
                 cap_ms: float | None = None) -> float:
    """
    Latency. `median_ms` is the p50; sigma controls tail weight.
    sigma 0.35 = tight (local tool call), 0.8 = wild (cold-start LLM).
    """
    v = rnd.lognormvariate(math.log(median_ms), sigma)
    if cap_ms:
        v = min(v, cap_ms)
    return max(1.0, v)


def beta(rnd: random.Random, a: float, b: float) -> float:
    return rnd.betavariate(a, b)


def retrieval_scores(rnd: random.Random, k: int, quality: float = 0.8) -> list[float]:
    """
    Returns k scores in descending order.

    `quality` in [0,1] is how well the corpus covers the query. High quality
    gives a top score near 0.85 with a clear gap to the rest — the signature of
    a good hit. Low quality gives a flat, low band around 0.2, which is exactly
    what a retrieval miss looks like on a chart: no gap, no winner.
    """
    a = 2.0 + 12.0 * quality
    b = 2.0 + 12.0 * (1.0 - quality)
    scores = sorted((beta(rnd, a, b) for _ in range(k)), reverse=True)
    if quality > 0.6:
        # good retrieval separates the top hit from the pack
        scores[0] = min(0.98, scores[0] + rnd.uniform(0.03, 0.10))
    return [round(s, 4) for s in scores]


def input_tokens(rnd: random.Random, query_chars: int, n_docs: int,
                 doc_chars: int = 1200, system_chars: int = 900) -> int:
    """
    Input tokens are *derived*, not drawn. A longer query and more retrieved
    context genuinely cost more, so cost correlates with retrieval depth the way
    it does in a real app. ~4 chars per token, plus chat scaffolding overhead.
    """
    chars = query_chars + system_chars + n_docs * doc_chars
    tok = chars / 3.9
    return int(max(50, rnd.gauss(tok, tok * 0.06)))


def output_tokens(rnd: random.Random, max_tokens: int = 1024,
                  verbosity: float = 1.0) -> int:
    """Right-skewed: most answers are short, a few ramble to the cap."""
    v = rnd.lognormvariate(math.log(160 * verbosity), 0.62)
    return int(min(max_tokens, max(8, v)))


def ttft_ms(rnd: random.Random, in_tok: int) -> float:
    """Time to first token scales with prompt length — prefill is real work."""
    base = 180 + in_tok * 0.055
    return lognormal_ms(rnd, base, sigma=0.38)


def decode_ms(rnd: random.Random, out_tok: int, tok_per_sec: float = 62.0) -> float:
    rate = max(8.0, rnd.gauss(tok_per_sec, tok_per_sec * 0.18))
    return (out_tok / rate) * 1000.0


def diurnal_weight(hour_float: float) -> float:
    """
    Traffic multiplier over a 24h clock. Two humps (morning ramp, afternoon
    peak), a lunch dip, and a quiet night floor. Backfilled volume charts look
    like a real product instead of a flat line.
    """
    h = hour_float % 24.0
    morning = math.exp(-((h - 10.0) ** 2) / 8.0)
    afternoon = math.exp(-((h - 15.5) ** 2) / 10.0)
    evening = 0.45 * math.exp(-((h - 20.5) ** 2) / 6.0)
    return 0.08 + 0.92 * min(1.0, morning + afternoon + evening)


def weighted_choice(rnd: random.Random, table: dict) -> str:
    keys = list(table.keys())
    weights = [table[k] for k in keys]
    return rnd.choices(keys, weights=weights, k=1)[0]


def jitter(rnd: random.Random, value: float, pct: float = 0.15) -> float:
    return value * rnd.uniform(1 - pct, 1 + pct)
