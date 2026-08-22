"""
Wire schema for the Flight Recorder.

Deliberately a strict subset of the OpenTelemetry GenAI semantic conventions so
that anything already emitting OTel spans can be pointed at this collector with
no code change. Attribute names are the OTel ones verbatim; the extra fields we
need that OTel has no convention for live under the `fr.*` namespace.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

# --- span kinds -------------------------------------------------------------
# These map onto what an AI application actually does. Everything the RCA engine
# knows how to reason about keys off these.
KIND_LLM = "llm"              # a model call
KIND_RETRIEVAL = "retrieval"  # vector / keyword / hybrid search
KIND_EMBEDDING = "embedding"
KIND_TOOL = "tool"            # function call, API call, code exec
KIND_AGENT = "agent"          # a planning / decision step
KIND_GUARDRAIL = "guardrail"  # moderation, injection check, schema validation
KIND_CHAIN = "chain"          # user-defined grouping

ALL_KINDS = {
    KIND_LLM, KIND_RETRIEVAL, KIND_EMBEDDING,
    KIND_TOOL, KIND_AGENT, KIND_GUARDRAIL, KIND_CHAIN,
}

STATUS_OK = "ok"
STATUS_ERROR = "error"


def new_id(n: int = 16) -> str:
    return uuid.uuid4().hex[:n]


def now_ns() -> int:
    return time.time_ns()


@dataclass
class Payload:
    """
    The sensitive half of a span: prompts, completions, retrieved chunks, tool
    args. Stored in a separate table with its own retention clock and its own
    RBAC capability, because 95% of debugging needs metadata only.
    """
    span_id: str
    trace_id: str
    tenant_id: str
    role: str                       # input | output | context | args | result
    content: str                    # ALREADY REDACTED by the time it lands
    redaction_report: dict = field(default_factory=dict)
    created_ns: int = field(default_factory=now_ns)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Span:
    trace_id: str
    span_id: str
    name: str
    kind: str
    start_ns: int
    end_ns: int
    tenant_id: str = ""
    parent_span_id: Optional[str] = None
    status: str = STATUS_OK
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)
    payloads: list[Payload] = field(default_factory=list)

    # -- derived -------------------------------------------------------------
    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1e6

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_ms"] = self.duration_ms
        return d

    def validate(self) -> list[str]:
        errs = []
        if self.kind not in ALL_KINDS:
            errs.append(f"unknown kind {self.kind!r}")
        if self.end_ns < self.start_ns:
            errs.append("end_ns before start_ns")
        if not self.trace_id or not self.span_id:
            errs.append("missing ids")
        return errs


# --- OTel GenAI attribute keys we care about --------------------------------
A_SYSTEM = "gen_ai.system"                    # openai | anthropic | bedrock ...
A_MODEL = "gen_ai.request.model"
A_TEMP = "gen_ai.request.temperature"
A_MAX_TOK = "gen_ai.request.max_tokens"
A_IN_TOK = "gen_ai.usage.input_tokens"
A_OUT_TOK = "gen_ai.usage.output_tokens"
A_FINISH = "gen_ai.response.finish_reasons"
A_OPERATION = "gen_ai.operation.name"

# fr.* — things OTel has no convention for yet but a RAG/agent debugger needs
A_RETRIEVED_K = "fr.retrieval.k"
A_TOP_SCORE = "fr.retrieval.top_score"
A_MEAN_SCORE = "fr.retrieval.mean_score"
A_DOC_IDS = "fr.retrieval.doc_ids"
A_INDEX = "fr.retrieval.index"
A_TOOL_NAME = "fr.tool.name"
A_TOOL_ARGS_HASH = "fr.tool.args_hash"        # for loop detection w/o payloads
A_AGENT_STEP = "fr.agent.step"
A_GUARD_VERDICT = "fr.guardrail.verdict"      # pass | block | flag
A_GUARD_RULE = "fr.guardrail.rule"
A_COST_USD = "fr.cost.usd"
A_PII_FOUND = "fr.redaction.findings"         # {"EMAIL": 2, "CREDIT_CARD": 1}
A_PII_ESCAPED = "fr.redaction.escaped"        # collector caught what SDK missed
A_USER_REF = "fr.user.ref"                    # pseudonymous, for erasure
A_SESSION = "fr.session.id"
A_ENV = "fr.env"                              # prod | staging | dev
A_RELEASE = "fr.release"


# --- pricing ----------------------------------------------------------------
# USD per 1M tokens. Swap for a live pricing service in production; hardcoded
# here so the cost observatory works offline during a demo.
PRICING = {
    "gpt-4o":              (2.50, 10.00),
    "gpt-4o-mini":         (0.15,  0.60),
    "claude-sonnet-4-6":   (3.00, 15.00),
    "claude-haiku-4-5":    (0.80,  4.00),
    "claude-opus-4-1":     (15.00, 75.00),
    "text-embedding-3-small": (0.02, 0.0),
    "llama-3.1-70b":       (0.59,  0.79),
}


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    return round((in_tok / 1e6) * p[0] + (out_tok / 1e6) * p[1], 6)
