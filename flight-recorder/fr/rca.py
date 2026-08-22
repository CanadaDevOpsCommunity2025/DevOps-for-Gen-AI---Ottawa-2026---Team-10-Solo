"""
Root-cause rules.

Rules, not an LLM. They're deterministic, they run in microseconds, they explain
themselves, and they never hallucinate a verdict on stage. An LLM belongs *on
top* of this — turning a verdict into prose — never underneath it as the
detector.

Each rule takes the span list for one trace and returns a Verdict or None.
First match wins, so order encodes priority: a leaked credential matters more
than a slow span, even when both are true.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from collections import Counter

from fr.schema import (
    A_TOP_SCORE, A_MEAN_SCORE, A_RETRIEVED_K, A_TOOL_ARGS_HASH, A_COST_USD,
    A_IN_TOK, A_GUARD_VERDICT, A_GUARD_RULE, A_PII_ESCAPED,
    STATUS_ERROR,
)

GROUNDING_FLOOR = 0.45      # below this, retrieved context isn't supporting an answer
LOOP_THRESHOLD = 3          # identical tool args this many times is a loop
BOTTLENECK_SHARE = 0.55     # one span eating this much of wall clock
COST_MULTIPLE = 3.0         # versus the tenant's rolling baseline


@dataclass
class Verdict:
    code: str
    title: str
    detail: str
    severity: str            # critical | warning | info
    evidence: list           # span_ids to highlight in the waterfall
    confidence: float

    def to_dict(self):
        return asdict(self)


def _kind(spans, kind):
    return [s for s in spans if s.get("kind") == kind]


def _root(spans):
    return next((s for s in spans if not s.get("parent_span_id")), spans[0])


# --- rules, in priority order ----------------------------------------------

def r_leakage(spans, ctx):
    hits = [s for s in spans if s.get("attrs", {}).get(A_PII_ESCAPED)]
    if not hits:
        return None
    labels = Counter()
    for s in hits:
        for k, v in (s["attrs"].get("fr.redaction.findings") or {}).items():
            labels[k] += v
    kinds = ", ".join(f"{v}x {k.lower().replace('_',' ')}" for k, v in labels.most_common(3))
    return Verdict(
        "leakage_risk", "Sensitive data reached the collector unredacted",
        f"The SDK should have masked this in-process. Second-pass scan caught "
        f"{kinds or 'sensitive values'} across {len(hits)} span(s) and redacted "
        f"server-side. Check the SDK version on this release.",
        "critical", [s["span_id"] for s in hits], 0.99)


def r_error(spans, ctx):
    errs = sorted((s for s in spans if s.get("status") == STATUS_ERROR
                   and s.get("parent_span_id")), key=lambda s: s["start_ns"])
    if not errs:
        return None
    first = errs[0]
    downstream = len(errs) - 1
    tail = f" {downstream} later span(s) failed as a consequence." if downstream else ""
    return Verdict(
        "error_propagation", f"{first.get('error_type') or 'Error'} in {first['name']}",
        f"{first.get('error_message') or 'span failed'}. This is the earliest "
        f"failure in the trace, so it's the root, not a symptom.{tail}",
        "critical", [s["span_id"] for s in errs], 0.95)


def r_guardrail(spans, ctx):
    blocked = [s for s in _kind(spans, "guardrail")
               if s.get("attrs", {}).get(A_GUARD_VERDICT) == "block"]
    if not blocked:
        return None
    rule = blocked[0]["attrs"].get(A_GUARD_RULE, "policy")
    return Verdict(
        "guardrail_block", f"Blocked by {rule.replace('_',' ')} guardrail",
        "The request was stopped before completion. This is the system working "
        "as designed — surfaced so you can tell a block apart from a crash.",
        "info", [s["span_id"] for s in blocked], 0.97)


def r_ungrounded(spans, ctx):
    rets = _kind(spans, "retrieval")
    llms = _kind(spans, "llm")
    if not rets or not llms:
        return None
    ret = rets[0]
    top = ret.get("attrs", {}).get(A_TOP_SCORE)
    if top is None or top >= GROUNDING_FLOOR:
        return None
    # did the model answer anyway, rather than refusing?
    answered = [s for s in llms if (s.get("attrs", {}).get("gen_ai.usage.output_tokens") or 0) > 40]
    if not answered:
        return None
    k = ret["attrs"].get(A_RETRIEVED_K, "?")
    mean = ret["attrs"].get(A_MEAN_SCORE)
    return Verdict(
        "ungrounded_generation", "Model answered without supporting context",
        f"Top retrieval score was {top:.2f} across {k} documents "
        f"(mean {mean:.2f}) — below the {GROUNDING_FLOOR} grounding floor. "
        f"The model produced {answered[-1]['attrs'].get('gen_ai.usage.output_tokens')} "
        f"tokens anyway. This is the classic RAG hallucination shape.",
        "critical", [ret["span_id"]] + [s["span_id"] for s in answered], 0.88)


def r_loop(spans, ctx):
    tools = _kind(spans, "tool")
    if len(tools) < LOOP_THRESHOLD:
        return None
    counts = Counter(s.get("attrs", {}).get(A_TOOL_ARGS_HASH) for s in tools)
    h, n = counts.most_common(1)[0]
    if n < LOOP_THRESHOLD or not h:
        return None
    hits = [s for s in tools if s["attrs"].get(A_TOOL_ARGS_HASH) == h]
    wasted = sum(s.get("duration_ms", 0) for s in hits[1:])
    return Verdict(
        "agent_loop", f"Agent called {hits[0]['name']} {n} times with identical arguments",
        f"Same argument hash ({h[:8]}) every call, so each retry could only "
        f"return the same result. About {wasted:.0f}ms wasted. Add a repeat "
        f"guard or a max-steps cap on the planner.",
        "warning", [s["span_id"] for s in hits], 0.93)


def r_cost(spans, ctx):
    root = _root(spans)
    cost = root.get("cost_usd") or 0
    base = ctx.get("cost_p50") or 0
    if not base or cost < base * COST_MULTIPLE:
        return None
    biggest = max(_kind(spans, "llm"),
                  key=lambda s: s.get("attrs", {}).get(A_IN_TOK, 0), default=None)
    tok = biggest["attrs"].get(A_IN_TOK) if biggest else 0
    return Verdict(
        "cost_anomaly", f"Trace cost ${cost:.4f} — {cost/base:.1f}x the tenant median",
        f"Largest prompt was {tok:,} input tokens in {biggest['name'] if biggest else 'an LLM call'}. "
        f"Usually a context window that grew unbounded: too many retrieved "
        f"documents, or conversation history never truncated.",
        "warning", [biggest["span_id"]] if biggest else [], 0.85)


def r_bottleneck(spans, ctx):
    root = _root(spans)
    total = root.get("duration_ms") or 0
    kids = [s for s in spans if s.get("parent_span_id")]
    if not kids or total <= 0:
        return None
    slow = max(kids, key=lambda s: s.get("duration_ms", 0))
    share = slow.get("duration_ms", 0) / total
    if share < BOTTLENECK_SHARE or total < 3000:
        return None
    return Verdict(
        "latency_bottleneck", f"{slow['name']} consumed {share:.0%} of the trace",
        f"{slow['duration_ms']:.0f}ms of {total:.0f}ms total. Everything else "
        f"combined took {total - slow['duration_ms']:.0f}ms — optimising "
        f"anywhere else won't move the number.",
        "warning", [slow["span_id"]], 0.90)


RULES = [r_leakage, r_error, r_guardrail, r_ungrounded, r_loop, r_cost, r_bottleneck]


def analyse(spans: list[dict], ctx: dict | None = None) -> dict | None:
    """First matching rule wins. ctx carries tenant baselines (cost_p50 etc)."""
    ctx = ctx or {}
    if not spans:
        return None
    for rule in RULES:
        try:
            v = rule(spans, ctx)
        except Exception:
            continue          # a broken rule must never break the trace view
        if v:
            return v.to_dict()
    return None


def healthy() -> dict:
    return Verdict("nominal", "No issues detected",
                   "All spans completed, retrieval was well grounded, cost and "
                   "latency within normal range for this tenant.",
                   "ok", [], 1.0).to_dict()
