"""
The synthetic RAG agent.

Emits the canonical shape:

    chain  support_agent
      ├─ agent      plan
      ├─ embedding  embed_query
      ├─ retrieval  vector_search
      ├─ llm        draft_answer
      ├─ guardrail  policy_check
      ├─ tool       account_lookup      (×N under tool_loop)
      └─ llm        final_answer

Two properties worth defending:

1. Spans are built on a real wall clock offset, so a waterfall renders with
   correct gaps and nesting rather than everything starting at t=0.

2. Values are *derived*, not independently sampled. Retrieving more documents
   raises input tokens, which raises cost and prefill latency. So when
   cost_spike inflates the context, latency moves too — because that is what
   actually happens. Independently sampled telemetry falls apart the moment
   someone plots cost against duration.
"""
from __future__ import annotations

import hashlib
import random

from fr.schema import (
    Span, Payload, new_id, cost_usd,
    KIND_LLM, KIND_RETRIEVAL, KIND_EMBEDDING, KIND_TOOL,
    KIND_AGENT, KIND_GUARDRAIL, KIND_CHAIN,
    STATUS_OK, STATUS_ERROR,
    A_SYSTEM, A_MODEL, A_TEMP, A_MAX_TOK, A_IN_TOK, A_OUT_TOK, A_FINISH,
    A_OPERATION, A_RETRIEVED_K, A_TOP_SCORE, A_MEAN_SCORE, A_DOC_IDS, A_INDEX,
    A_TOOL_NAME, A_TOOL_ARGS_HASH, A_AGENT_STEP, A_GUARD_VERDICT, A_GUARD_RULE,
    A_COST_USD, A_USER_REF, A_SESSION, A_ENV, A_RELEASE,
)
from harness import corpus as C
from harness import distributions as D
from harness.scenarios import Scenario

MS = 1_000_000  # ns per ms


class TraceBuilder:
    """Accumulates spans on a monotonically advancing cursor."""

    def __init__(self, trace_id: str, tenant_id: str, start_ns: int):
        self.trace_id = trace_id
        self.tenant_id = tenant_id
        self.cursor = start_ns
        self.spans: list[Span] = []
        self.root_id = new_id()

    def gap(self, ms: float):
        self.cursor += int(ms * MS)

    def add(self, name: str, kind: str, dur_ms: float, attrs: dict,
            parent: str | None = None, status: str = STATUS_OK,
            error: tuple[str, str] | None = None) -> Span:
        start = self.cursor
        end = start + int(dur_ms * MS)
        sp = Span(
            trace_id=self.trace_id, span_id=new_id(), name=name, kind=kind,
            start_ns=start, end_ns=end, tenant_id=self.tenant_id,
            parent_span_id=parent or self.root_id,
            status=status,
            error_type=error[0] if error else None,
            error_message=error[1] if error else None,
            attrs=attrs,
        )
        self.spans.append(sp)
        self.cursor = end
        return sp


def _args_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _user_ref(rnd: random.Random) -> str:
    return "u_" + hashlib.sha256(str(rnd.randrange(1, 900)).encode()).hexdigest()[:10]


def generate_trace(rnd: random.Random, sc: Scenario, start_ns: int,
                   tenant: dict | None = None) -> tuple[list[Span], dict]:
    """Returns (spans, meta). meta carries expectations for the self-check."""

    tenant = tenant or rnd.choices(
        C.TENANTS, weights=[t["weight"] for t in C.TENANTS], k=1)[0]
    tid = tenant["tenant_id"]
    trace_id = new_id(24)
    b = TraceBuilder(trace_id, tid, start_ns)

    env = D.weighted_choice(rnd, C.ENVIRONMENTS)
    release = D.weighted_choice(rnd, C.RELEASES)

    # A planted cost regression. The newer release shifted traffic toward larger
    # models and grew the system prompt — the single most common way an AI app's
    # bill doubles between Fridays. Your cost view exists to catch exactly this,
    # so the data needs to actually contain it.
    if release == C.REGRESSED_RELEASE:
        model = D.weighted_choice(rnd, C.MODELS_AFTER)
        system_chars = 2400
    else:
        model = D.weighted_choice(rnd, C.MODELS)
        system_chars = 900
    session = "s_" + new_id(10)
    user_ref = _user_ref(rnd)
    index = rnd.choice(C.INDEXES)

    common = {A_ENV: env, A_RELEASE: release, A_SESSION: session, A_USER_REF: user_ref}

    # ---- pick the user turn -------------------------------------------------
    if sc.prompt_injection:
        query = rnd.choice(C.INJECTIONS)
        covered, base_quality = False, 0.15
    elif sc.inject_raw_pii or rnd.random() < 0.18:
        query = rnd.choice(C.PII_QUERIES)
        covered, base_quality = True, rnd.uniform(0.6, 0.85)
    else:
        query, covered, base_quality = rnd.choice(C.QUERIES)

    quality = sc.retrieval_quality if sc.retrieval_quality is not None else base_quality
    quality = max(0.02, min(0.98, D.jitter(rnd, quality, 0.12)))
    payloads: list[Payload] = []

    def pay(span: Span, role: str, content: str):
        payloads.append(Payload(span_id=span.span_id, trace_id=trace_id,
                                tenant_id=tid, role=role, content=content))

    # ---- root ---------------------------------------------------------------
    root_start = b.cursor

    # ---- agent: plan --------------------------------------------------------
    plan_ms = D.lognormal_ms(rnd, 42, 0.5)
    lm = sc.latency_multiplier
    sp_plan = b.add("plan", KIND_AGENT, plan_ms * lm.get("plan", 1.0),
                    {**common, A_AGENT_STEP: 1,
                     A_OPERATION: "plan",
                     "fr.agent.strategy": "retrieve_then_answer"})
    pay(sp_plan, "input", query)
    b.gap(rnd.uniform(1, 6))

    # ---- embedding ----------------------------------------------------------
    q_tok = max(6, int(len(query) / 3.9))
    emb_ms = D.lognormal_ms(rnd, 38, 0.4)
    b.add("embed_query", KIND_EMBEDDING, emb_ms * lm.get("embed_query", 1.0),
          {**common, A_SYSTEM: "openai", A_MODEL: C.EMBED_MODEL,
           A_IN_TOK: q_tok, A_OUT_TOK: 0, A_OPERATION: "embeddings",
           A_COST_USD: cost_usd(C.EMBED_MODEL, q_tok, 0),
           "fr.embedding.dimensions": 1536})
    b.gap(rnd.uniform(1, 4))

    # ---- retrieval ----------------------------------------------------------
    k = rnd.choice([3, 5, 5, 5, 8])
    scores = D.retrieval_scores(rnd, k, quality)
    if sc.force_top_score is not None:
        scores = sorted([min(s, sc.force_top_score) for s in scores], reverse=True)
        scores[0] = sc.force_top_score
    docs = rnd.sample(C.DOCS, k)
    # retrieval latency grows with k, sub-linearly
    ret_ms = D.lognormal_ms(rnd, 55 + 9 * k, 0.42)
    sp_ret = b.add("vector_search", KIND_RETRIEVAL,
                   ret_ms * lm.get("vector_search", 1.0),
                   {**common, A_INDEX: index, A_RETRIEVED_K: k,
                    A_TOP_SCORE: round(scores[0], 4),
                    A_MEAN_SCORE: round(sum(scores) / len(scores), 4),
                    A_DOC_IDS: [d[0] for d in docs],
                    "fr.retrieval.strategy": "hybrid_bm25_dense",
                    "fr.retrieval.scores": scores})
    pay(sp_ret, "context",
        "\n---\n".join(f"[{d[0]}] {d[1]}: policy text excerpt…" for d in docs))
    b.gap(rnd.uniform(2, 9))

    # ---- llm: draft ---------------------------------------------------------
    n_ctx = k if quality > 0.35 else max(1, k // 2)
    in_tok = int(D.input_tokens(rnd, len(query), n_ctx,
                                system_chars=system_chars) * sc.input_token_multiplier)
    verbosity = tenant["verbosity"]
    out_tok = D.output_tokens(rnd, verbosity=verbosity)

    draft_err = sc.error_on == "draft_answer"
    if draft_err:
        out_tok = 0
    tps = C.TOK_PER_SEC[model]
    draft_ms = (D.ttft_ms(rnd, in_tok)
                + D.decode_ms(rnd, out_tok, tps)) * lm.get("draft_answer", 1.0)

    err = None
    if draft_err:
        err = next(e for e in C.ERROR_SHAPES if e[0] == sc.error_kind)

    sp_draft = b.add("draft_answer", KIND_LLM, draft_ms,
                     {**common, A_SYSTEM: C.SYSTEMS[model], A_MODEL: model,
                      A_TEMP: round(rnd.uniform(0.0, 0.7), 2), A_MAX_TOK: 1024,
                      A_IN_TOK: in_tok, A_OUT_TOK: out_tok,
                      A_OPERATION: "chat",
                      A_FINISH: ["error"] if draft_err else ["stop"],
                      A_COST_USD: cost_usd(model, in_tok, out_tok),
                      "fr.llm.ttft_ms": round(D.ttft_ms(rnd, in_tok), 1)},
                     status=STATUS_ERROR if draft_err else STATUS_OK,
                     error=err)

    if sc.ungrounded_answer:
        draft = rnd.choice(C.UNGROUNDED_ANSWERS)
    elif sc.prompt_injection:
        draft = "I can't follow those instructions. Here's what I can help with instead…"
    elif not covered and quality < 0.35:
        draft = rnd.choice(C.REFUSALS)
    else:
        draft = rnd.choice(C.GOOD_ANSWERS)

    if sc.inject_raw_pii:
        # The whole point: the SDK is *supposed* to strip this. When it lands at
        # the collector still readable, that's a redaction escape.
        draft += ("\n\nI've emailed a copy to priya.raghavan@fabrikam.co.uk "
                  "and confirmed card 4532015112830366 on file.")

    if not draft_err:
        pay(sp_draft, "output", draft)

    dropped = set(sc.drop_spans)

    # ---- guardrail ----------------------------------------------------------
    if "policy_check" not in dropped:
        b.gap(rnd.uniform(1, 4))
        verdict = sc.guardrail_verdict or ("flag" if sc.inject_raw_pii else "pass")
        rule = ("prompt_injection" if sc.prompt_injection
                else "pii_egress" if sc.inject_raw_pii
                else rnd.choice(C.GUARDRAILS))
        b.add("policy_check", KIND_GUARDRAIL,
              D.lognormal_ms(rnd, 24, 0.35) * lm.get("policy_check", 1.0),
              {**common, A_GUARD_VERDICT: verdict, A_GUARD_RULE: rule,
               "fr.guardrail.score": round(rnd.uniform(0.6, 0.99), 3)})

    # ---- tool(s) ------------------------------------------------------------
    tool_name, _, tool_med = rnd.choices(
        C.TOOLS, weights=[t[1] for t in C.TOOLS], k=1)[0]
    if sc.error_on and sc.error_on in [t[0] for t in C.TOOLS]:
        tool_name = sc.error_on
        tool_med = next(t[2] for t in C.TOOLS if t[0] == tool_name)

    if tool_name not in dropped:
        fixed_args = f'{{"account_id":"ACC-{rnd.randrange(10000,99999)}"}}'
        for i in range(sc.tool_repeats):
            b.gap(rnd.uniform(2, 8))
            args = fixed_args if sc.identical_tool_args else \
                f'{{"account_id":"ACC-{rnd.randrange(10000,99999)}","attempt":{i}}}'
            is_err = sc.error_on == tool_name and i == sc.tool_repeats - 1
            terr = next((e for e in C.ERROR_SHAPES if e[0] == sc.error_kind), None) if is_err else None
            sp_tool = b.add(tool_name, KIND_TOOL,
                            D.lognormal_ms(rnd, tool_med, 0.55) * lm.get(tool_name, 1.0),
                            {**common, A_TOOL_NAME: tool_name,
                             A_TOOL_ARGS_HASH: _args_hash(args),
                             "fr.tool.attempt": i + 1,
                             "fr.tool.kind": "http"},
                            status=STATUS_ERROR if is_err else STATUS_OK,
                            error=terr)
            pay(sp_tool, "args", args)

    # ---- llm: final ---------------------------------------------------------
    final_answer = None
    if "final_answer" not in dropped:
        b.gap(rnd.uniform(2, 10))
        f_in = int(D.input_tokens(rnd, len(query), n_ctx + 1,
                                  system_chars=system_chars) * sc.input_token_multiplier)
        f_out = D.output_tokens(rnd, verbosity=verbosity * 0.8)
        f_ms = (D.ttft_ms(rnd, f_in)
                + D.decode_ms(rnd, f_out, tps)) * lm.get("final_answer", 1.0)
        sp_final = b.add("final_answer", KIND_LLM, f_ms,
                         {**common, A_SYSTEM: C.SYSTEMS[model], A_MODEL: model,
                          A_TEMP: round(rnd.uniform(0.0, 0.4), 2), A_MAX_TOK: 1024,
                          A_IN_TOK: f_in, A_OUT_TOK: f_out, A_OPERATION: "chat",
                          A_FINISH: ["stop"],
                          A_COST_USD: cost_usd(model, f_in, f_out)})
        final_answer = draft if sc.ungrounded_answer else draft
        pay(sp_final, "output", final_answer)

    # ---- root span ----------------------------------------------------------
    total_cost = round(sum(s.attrs.get(A_COST_USD, 0.0) for s in b.spans), 6)
    any_error = any(s.status == STATUS_ERROR for s in b.spans)
    root = Span(
        trace_id=trace_id, span_id=b.root_id, name="support_agent",
        kind=KIND_CHAIN, start_ns=root_start, end_ns=b.cursor,
        tenant_id=tid, parent_span_id=None,
        status=STATUS_ERROR if any_error else STATUS_OK,
        attrs={**common, A_COST_USD: total_cost,
               "fr.scenario": sc.key,
               "fr.span_count": len(b.spans) + 1,
               "fr.trace.model": model},
    )

    # attach payloads to their owning spans
    by_span: dict[str, list[Payload]] = {}
    for p in payloads:
        by_span.setdefault(p.span_id, []).append(p)
    for s in b.spans:
        s.payloads = by_span.get(s.span_id, [])

    spans = [root] + b.spans
    meta = {
        "trace_id": trace_id, "tenant_id": tid, "scenario": sc.key,
        "expect_verdict": sc.expect_verdict, "cost_usd": total_cost,
        "duration_ms": round((b.cursor - root_start) / 1e6, 2),
        "status": root.status, "model": model, "env": env, "release": release,
        "top_score": sp_ret.attrs[A_TOP_SCORE],
        "span_count": len(spans),
    }
    return spans, meta
