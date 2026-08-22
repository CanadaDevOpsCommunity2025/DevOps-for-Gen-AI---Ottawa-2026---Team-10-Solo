# AI Application Flight Recorder

**New here? Read `START.md`.** This file covers the traffic generator (M3);
`RUN.md` documents every CLI command with its output.

## M3 — Traffic generator

Synthetic traffic for the AI Application Flight Recorder. Emits realistic RAG-agent
traces so you can build the collector, RCA engine and dashboard against data that
already exists.

## Quickstart

```bash
pip install fastapi uvicorn          # only needed later, for M2/M4
python harness/run.py --selfcheck    # assert every scenario deforms correctly
python harness/run.py --backfill 6h --backfill-count 900 --count 0 --rate 0
python harness/explore.py --summary
```

Ninety seconds from clone to a database with 900 traces spanning six hours,
three tenants, four models, two releases and nine distinct failure modes.

## The acceptance test

```bash
python harness/run.py --rate 3 --scenario mixed --duration 60
```

180 traces in 60s at a paced 3/s, distributed across tenants by weight.

## Trace shape

```
chain      support_agent
  agent      plan
  embedding  embed_query
  retrieval  vector_search        k, top_score, mean_score, doc_ids
  llm        draft_answer         in/out tokens, cost, ttft
  guardrail  policy_check         rule, verdict
  tool       account_lookup       args_hash  (×6 under tool_loop)
  llm        final_answer
```

Attribute names are OpenTelemetry GenAI semantic conventions verbatim
(`gen_ai.request.model`, `gen_ai.usage.input_tokens`). Everything OTel has no
convention for sits under `fr.*`. That means anything already emitting OTel can
point at your collector unchanged, and your M4 dashboard is reading real
industry-standard keys rather than a schema you invented.

## Scenarios

| key | what it deforms | expected verdict |
|---|---|---|
| `nominal` | nothing | — |
| `retrieval_miss` | top_score → 0.21, model answers confidently anyway | `ungrounded_generation` |
| `tool_loop` | same tool ×6, identical `args_hash` | `agent_loop` |
| `cost_spike` | input tokens ×20, latency follows | `cost_anomaly` |
| `pii_leak` | raw email + Luhn-valid card in the completion | `leakage_risk` |
| `timeout` | tool errors, `final_answer` never runs | `error_propagation` |
| `prompt_injection` | guardrail blocks, trace truncates | `guardrail_block` |
| `rate_limit` | 429 mid-trace, three spans never happen | `error_propagation` |
| `latency_spike` | one span eats 80%+ of wall clock | `latency_bottleneck` |

`--scenario mixed` uses production base rates (68% healthy).
`--scenario demo` compresses them so every failure mode appears within ~20 traces.

## Why the data holds up under a chart

Values are derived, not independently sampled:

- Input tokens come from query length + retrieved doc count, so cost correlates
  with retrieval depth.
- LLM latency is time-to-first-token (scales with prompt size) plus decode time
  (output tokens ÷ model throughput). `gpt-4o-mini` streams at 148 tok/s,
  `claude-sonnet-4-6` at 64 — so model choice visibly trades cost against latency.
- Retrieval scores are beta-distributed. Good coverage gives a top hit near 0.85
  with a clear gap to the pack; a miss gives a flat low band with no winner.
- Backfilled volume follows a diurnal curve with a morning ramp, afternoon peak
  and night floor.
- Release `2026.8.19` costs 2.1x per trace than `2026.8.14` — a planted cost
  regression (bigger models, longer system prompt) for your cost view to catch.
- `--seed` reproduces any run exactly. Use it when rehearsing.

Measured on 900 backfilled traces: nominal p50 4.0s, p90 7.6s, p99 14.0s.

## Sinks

```bash
--sink sqlite                          # default, flightrecorder.db
--sink http --url http://localhost:8000   # POST /v1/traces, once M2 exists
--sink sentinel                        # POST to the Sentinel observability platform (../platform)
--sink jsonl                           # traces.jsonl, for replay
--sink stdout                          # one line per trace
--sink sqlite,sentinel                 # fan out — keep this console AND Sentinel populated
```

The HTTP sink ships `pii_leak` payloads **unredacted on purpose**. A correct
collector must catch them on its second-pass scan and set
`fr.redaction.escaped`. That's your zero-trust seam, and this is how you test it.

## Inspecting

```bash
python harness/explore.py                              # recent traces
python harness/explore.py --scenario tool_loop         # waterfall for that failure
python harness/explore.py --scenario pii_leak --payloads
python harness/explore.py --summary                    # cost by tenant/model/release
python harness/explore.py <trace_id>
```

The ASCII waterfall is your fallback demo. If the dashboard breaks at 2am, this
still tells the whole story in a terminal.

## Bridge to Sentinel — two dashboards, two jobs

This console (`server.py` / `ui/`) is for **generating and eyeballing raw RAG
traces** — the waterfall, the span-level verdicts, the ATC-style live feed.
`../platform` (Sentinel) is the **product's observability dashboard** — PII
compliance drift, retention/region policy, audit trails, tenant isolation.
They stay two separate dashboards on purpose; this generator just also feeds
the second one, so a judge (or you) can see the same synthetic traffic show
up as real governance signal, not just a trace waterfall.

```bash
python3 register_with_sentinel.py          # once — registers one app per fr tenant with Sentinel
python3 harness/run.py --rate 3 --scenario mixed --sink sqlite,sentinel
```

`register_with_sentinel.py` logs into Sentinel as each tenant's app-owner
account (seeded by `platform/seed.js`) and registers **"RAG Support Agent
(flight-recorder)"**, deliberately declaring only `["EMAIL", "CREDIT_CARD"]`
as expected PII — narrower than what `pii_leak` and friends actually plant
(`SIN_CA`, `DOB`, `PHONE`, `PASSPORT`, `IPV4`, …). Writes the resulting
`appId`/`ingestKey` per tenant to `sentinel-config.json`.

The `sentinel` sink then, per trace:
- Forwards every payload (already redacted — either by this sink directly,
  or by an earlier `sqlite` sink in the same `--sink sqlite,sentinel` list)
  to Sentinel's `/api/ingest/telemetry`, so Sentinel's own drift detector
  compares what actually showed up against that declared profile and raises
  `undeclared_pii_detected` for anything not on the list.
- Runs `fr.rca.analyse()` — the same deterministic verdict engine this
  console uses — and, for anything other than `nominal`, posts it to
  Sentinel's `/api/ingest/audit`. `leakage_risk` and `error_propagation`
  land as `critical`, `ungrounded_generation` as `critical`, `agent_loop`/
  `cost_anomaly`/`latency_bottleneck` as `high`, `guardrail_block` as
  `info` — Sentinel's alert routing takes it from there (notifies the
  platform admin and the app's registered owner on high/critical).

Nothing here changes what this console shows you locally; it's strictly
additive.

## Mid-demo scenario injection

```bash
python harness/run.py --count 1 --scenario pii_leak --sink stdout,sqlite
```

Wire this to a button in the M4 dashboard header. A judge clicks **PII leak** and
watches the escaped-findings counter tick.

## Files

```
server.py           FastAPI console server (traces, RCA, RBAC, audit, inject)
ui/index.html       single-file dashboard, no build step
fr/rca.py           root-cause rules engine
fr/schema.py        Span/Payload model, OTel GenAI attribute keys, pricing
fr/store.py         SQLite; tenant_id is a required positional arg everywhere
fr/redaction.py     detectors + deterministic HMAC tokenisation
fr/governance.py    tenants, API keys, RBAC capabilities, retention policy
harness/corpus.py   queries, docs, tools, tenants, format-valid fake PII
harness/distributions.py   lognormal latency, beta scores, diurnal curve
harness/scenarios.py       failure modes as declarative deformations
harness/agent.py           builds the span tree
harness/sinks.py           sqlite | http | jsonl | stdout | null | fanout
harness/run.py             CLI: rate, backfill, workers, selfcheck
harness/explore.py         terminal waterfall + rollups
register_with_sentinel.py  registers one app per fr tenant with ../platform
sentinel-config.json       output of the above; read by the `sentinel` sink
RUN.md              every command with its expected output
```

## Known gaps

Names and street addresses aren't detected — those need NER, and
`fr/redaction.py` takes a pluggable `ner=` callable for Presidio or spaCy. Every
other planted identifier fires. "Priya Raghavan" in the corpus survives redaction
on purpose, so you have an honest answer when a judge asks what your regex misses.
