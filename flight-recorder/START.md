# Start here

```bash
cd flight-recorder
pip install fastapi uvicorn
python3 harness/run.py --backfill 6h --backfill-count 900 --backfill-only
python3 server.py
```

Open **http://localhost:8000**.

Three commands, about 90 seconds, and you have a console with 900 traces across
three tenants and nine failure modes already in it.

---

## What you get

**Instrument row** — traces, error rate, spend, latency, redaction escapes, and
release cost drift. The drift gauge reads ~2x because release `2026.8.19`
shifted traffic to larger models and grew the system prompt; that regression is
planted in the data so your cost view has something real to find.

**Live feed** — one ATC-style progress strip per trace. Coloured notch for
severity, trace id as the callsign, and a compressed span map as the route.
Polls every 2.5s.

**Detail panel** — root-cause verdict, waterfall with evidence spans
highlighted, redacted payloads, audit trail.

---

## The demo, in order

**1. Click a `retrieval miss` strip.**
Verdict: *Model answered without supporting context* — top retrieval score 0.21
across 3 documents, below the 0.45 grounding floor, model produced 100 tokens
anyway. The retrieval span and the LLM span are outlined in the waterfall.

**2. Click a `tool loop` strip.**
Six `account_lookup` spans, identical `args` hash on every one, so each retry
could only return the same result. The verdict names the wasted milliseconds.

**3. Press the `PII leak` inject button.**
A trace appears at the top of the feed within a second. Verdict:
*Sensitive data reached the collector unredacted.* The payload panel shows
`<EMAIL:6d23dc>` and `<CREDIT_CARD:bffa6c>` highlighted, with a `1x email,
1x credit card masked` chip. The escapes gauge ticks.

Then say the important part: the generator emitted that email **in the clear**
on purpose. The SDK should have caught it in-process. The collector's
second-pass scan caught it at ingest and masked it before it touched disk.
`SELECT * FROM payloads WHERE content LIKE '%@fabrikam%'` returns zero rows.

**4. Switch role to `responder`.**
Verdict and waterfall stay. Payloads lock: *Reading prompts and completions
needs the payload:read capability.* Seeing that a trace failed and seeing the
user content that failed are separate privileges.

**5. Switch role to `auditor`.**
The inverse — audit trail opens, traces close.

**6. Switch tenant to `zephyr`, then back.**
Different volume, cost and failure mix. A cross-tenant trace read returns
**404, not 403** — a 403 would confirm the trace exists.

---

## Verify it from a terminal

```bash
TID=$(curl -s "localhost:8000/api/traces?tenant=northwind&limit=60" \
  -H "X-FR-Role: developer" | python3 -c \
  "import json,sys;print([t['trace_id'] for t in json.load(sys.stdin)['traces'] if t['scenario']=='pii_leak'][0])")

curl -s -o /dev/null -w "responder → %{http_code}\n" \
  "localhost:8000/api/payloads/$TID?tenant=northwind" -H "X-FR-Role: responder"   # 403
curl -s -o /dev/null -w "developer → %{http_code}\n" \
  "localhost:8000/api/payloads/$TID?tenant=northwind" -H "X-FR-Role: developer"   # 200
curl -s -o /dev/null -w "wrong tenant → %{http_code}\n" \
  "localhost:8000/api/trace/$TID?tenant=zephyr" -H "X-FR-Role: developer"         # 404
```

```bash
sqlite3 flightrecorder.db \
  "SELECT COUNT(*) FROM payloads WHERE content LIKE '%@fabrikam%' \
   OR content LIKE '%4532015112830366%';"     # 0
```

---

## Endpoints

| method | path | capability |
|---|---|---|
| GET | `/api/tenants` | — |
| GET | `/api/stats?tenant=` | `trace:read` |
| GET | `/api/traces?tenant=&limit=` | `trace:read` |
| GET | `/api/trace/{id}?tenant=` | `trace:read` — returns spans + verdict |
| GET | `/api/payloads/{id}?tenant=` | `payload:read` |
| GET | `/api/audit` | `audit:read` |
| POST | `/api/inject?scenario=&count=` | — (demo only) |

Role arrives as `X-FR-Role` so the dropdown can switch it. In production it
comes from the API key; `fr/governance.py` already has that path.

---

## Verdicts

Rules, in priority order, first match wins. Deterministic, microseconds, and
they never hallucinate on stage. An LLM belongs on top of this turning a verdict
into prose — never underneath as the detector.

| verdict | fires when |
|---|---|
| `leakage_risk` | second-pass scan caught PII the SDK missed |
| `error_propagation` | earliest errored span, plus its downstream casualties |
| `guardrail_block` | a guardrail returned `block` — working as designed, not a crash |
| `ungrounded_generation` | top retrieval score < 0.45 and the model answered anyway |
| `agent_loop` | same tool `args_hash` 3+ times |
| `cost_anomaly` | trace cost > 3x the tenant median |
| `latency_bottleneck` | one span > 55% of wall clock |
| `nominal` | none of the above |

Thresholds are constants at the top of `fr/rca.py`.

---

## Running the generator alongside the console

```bash
# terminal 1
python3 server.py
# terminal 2 — live traffic while the console is open
python3 harness/run.py --rate 2 --scenario demo
```

`demo` compresses the failure mix so every mode appears within ~20 traces.
`mixed` uses production base rates (68% healthy).
