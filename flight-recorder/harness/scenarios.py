"""
Scenarios.

Each scenario is a *deformation* of the nominal trace, expressed as data rather
than as a separate code path. One generator function produces every shape; the
scenario only says which knobs move. That means adding a new failure mode is a
dict entry, not a new branch — which matters when a judge asks "can you show a
runaway context window?" and you have ninety seconds.

`mixed` samples from realistic base rates: most production traffic is fine, and
a dashboard where 1 in 6 traces is on fire looks like a toy.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Scenario:
    key: str
    label: str
    # what the RCA engine ought to conclude — used by the self-check in run.py
    expect_verdict: str | None = None

    retrieval_quality: float | None = None   # override corpus coverage
    force_top_score: float | None = None
    tool_repeats: int = 1
    identical_tool_args: bool = False
    input_token_multiplier: float = 1.0
    inject_raw_pii: bool = False             # payload leaves SDK *unredacted*
    error_on: str | None = None              # span name that fails
    error_kind: str | None = None
    latency_multiplier: dict = field(default_factory=dict)  # span name -> factor
    guardrail_verdict: str | None = None
    prompt_injection: bool = False
    ungrounded_answer: bool = False
    drop_spans: tuple = ()


SCENARIOS: dict[str, Scenario] = {

    "nominal": Scenario(
        key="nominal", label="Healthy request",
    ),

    "retrieval_miss": Scenario(
        key="retrieval_miss", label="Retrieval missed, model answered anyway",
        expect_verdict="ungrounded_generation",
        retrieval_quality=0.08, force_top_score=0.21,
        ungrounded_answer=True,
    ),

    "tool_loop": Scenario(
        key="tool_loop", label="Agent stuck calling the same tool",
        expect_verdict="agent_loop",
        tool_repeats=6, identical_tool_args=True,
        latency_multiplier={"final_answer": 1.4},
    ),

    "cost_spike": Scenario(
        key="cost_spike", label="Runaway context window",
        expect_verdict="cost_anomaly",
        input_token_multiplier=20.0,
        latency_multiplier={"draft_answer": 3.2, "final_answer": 2.8},
    ),

    "pii_leak": Scenario(
        key="pii_leak", label="Raw PII reached the completion",
        expect_verdict="leakage_risk",
        inject_raw_pii=True,
    ),

    "timeout": Scenario(
        key="timeout", label="Downstream tool timed out",
        expect_verdict="error_propagation",
        error_on="account_lookup", error_kind="UpstreamTimeout",
        latency_multiplier={"account_lookup": 9.0},
        drop_spans=("final_answer",),
    ),

    # --- extras that make the demo look deeper than a hackathon build -------

    "prompt_injection": Scenario(
        key="prompt_injection", label="Injection caught by guardrail",
        expect_verdict="guardrail_block",
        prompt_injection=True, guardrail_verdict="block",
        drop_spans=("account_lookup", "final_answer"),
    ),

    "rate_limit": Scenario(
        key="rate_limit", label="Provider 429 mid-trace",
        expect_verdict="error_propagation",
        error_on="draft_answer", error_kind="RateLimitError",
        latency_multiplier={"draft_answer": 0.3},
        drop_spans=("policy_check", "account_lookup", "final_answer"),
    ),

    "latency_spike": Scenario(
        key="latency_spike", label="Slow decode on the final call",
        expect_verdict="latency_bottleneck",
        latency_multiplier={"final_answer": 11.0},
    ),
}

# Base rates for `mixed`. Roughly what a real app in a bad week looks like.
MIXED_WEIGHTS = {
    "nominal":          0.68,
    "retrieval_miss":   0.09,
    "latency_spike":    0.05,
    "tool_loop":        0.04,
    "cost_spike":       0.04,
    "pii_leak":         0.04,
    "timeout":          0.03,
    "rate_limit":       0.02,
    "prompt_injection": 0.01,
}

# A tighter mix for demoing: every failure mode shows up within ~20 traces.
DEMO_WEIGHTS = {
    "nominal":          0.34,
    "retrieval_miss":   0.13,
    "tool_loop":        0.11,
    "cost_spike":       0.11,
    "pii_leak":         0.11,
    "timeout":          0.08,
    "latency_spike":    0.06,
    "rate_limit":       0.04,
    "prompt_injection": 0.02,
}


def resolve(name: str) -> Scenario | None:
    """None means 'sample per-trace' — the caller handles mixed/demo."""
    if name in ("mixed", "demo"):
        return None
    if name not in SCENARIOS:
        raise SystemExit(
            f"unknown scenario {name!r}\n"
            f"available: {', '.join(sorted(SCENARIOS))}, mixed, demo"
        )
    return SCENARIOS[name]


def weights_for(name: str) -> dict:
    return DEMO_WEIGHTS if name == "demo" else MIXED_WEIGHTS
