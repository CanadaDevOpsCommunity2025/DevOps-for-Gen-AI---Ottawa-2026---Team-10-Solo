# How to run

No dependencies. Python 3.10+, standard library only.

```bash
cd flight-recorder
python3 harness/run.py --selfcheck
```

---

## 1. Verify every failure mode still deforms correctly

```bash
python3 harness/run.py --selfcheck
```

Returns a pass/fail table. Run this before every demo — if a refactor silently
breaks a scenario, you find out here rather than on stage.

```
scenario           spans       ms      cost  check
------------------------------------------------------------------
nominal                8     4021   0.00099  PASS  shape only
retrieval_miss         8     2973   0.00211  PASS  top_score=0.21 (want <=0.25)
tool_loop             13     8994   0.00443  PASS  6 tool spans, 1 distinct args
cost_spike             8    29659   0.18662  PASS  cost=$0.1866 (want > $0.004)
pii_leak               8     9789   0.00094  PASS  raw PII present for collector to catch
timeout                7     6461   0.00921  PASS  status=error
prompt_injection       6     1272   0.00015  PASS  guardrail=block
rate_limit             5      196   0.00144  PASS  status=error
latency_spike          8     7911   0.00052  PASS  bottleneck=81% of trace
------------------------------------------------------------------
all scenarios deform correctly
```

Exit code 0 on pass, 1 on any failure — so you can wire it into CI.

---

## 2. Fill an empty database with realistic history

```bash
python3 harness/run.py --backfill 6h --backfill-count 900 --backfill-only
```

Takes about 6 seconds. Creates `flightrecorder.db`.

```
[backfill] 900 traces across 6h in 5.8s (156/s)

[done] 900 traces in 5.8s (154.6/s)
  nominal              612  68.0%
  retrieval_miss        81   9.0%
  latency_spike         45   5.0%
  tool_loop             38   4.2%
  cost_spike            36   4.0%
  pii_leak              35   3.9%
  timeout               27   3.0%
  rate_limit            17   1.9%
  prompt_injection       9   1.0%

[db] flightrecorder.db — 900 traces across 3 tenants
  acmehealth     289 traces  $  3.2466   18 errors     7692ms avg
  northwind      419 traces  $  4.3408   28 errors     6030ms avg
  zephyr         192 traces  $  2.1816   10 errors     4918ms avg
```

Timestamps are spread across the last 6 hours following a diurnal curve, so your
volume chart has a real shape the moment the UI loads.

---

## 3. Stream live traffic (the acceptance test)

```bash
python3 harness/run.py --rate 3 --scenario mixed --duration 60
```

Emits exactly 180 traces at a paced 3/s. Progress every 5 seconds; Ctrl-C stops
cleanly and still prints the rollup.

```
[stream] 15 traces  3.0/s  errors=4
[stream] 31 traces  3.0/s  errors=9
...
[done] 180 traces in 60.0s (3.0/s)
  nominal              130  72.2%
  retrieval_miss        13   7.2%
  ...
```

Add `-v` to watch each trace land:

```
ok  northwind   nominal              4021ms  $0.00435  top=0.87  3942b7b2eb05
ERR acmehealth  timeout              6461ms  $0.00921  top=0.79  8c1f0aa42e77
ok  zephyr      pii_leak             3148ms  $0.01890  top=0.82  e2888e2d0e69
```

Useful flags: `--poisson` for realistic bursty arrivals, `--flat` to disable
diurnal modulation, `--workers 4` for higher throughput, `--seed 42` to
reproduce a run exactly.

---

## 4. Look at what was generated

```bash
python3 harness/explore.py --summary
```

```
by tenant
  acmehealth      165 traces  $   1.9516     9 err     8263ms avg
  northwind       273 traces  $   3.3180    16 err     6191ms avg
  zephyr          104 traces  $   1.2410     9 err     4783ms avg

by scenario
  nominal                376      4903ms avg  $0.00697 avg
  retrieval_miss          32      4863ms avg  $0.00562 avg
  cost_spike              26     24505ms avg  $0.11711 avg
  pii_leak                25      4799ms avg  $0.00841 avg
  latency_spike           24     23448ms avg  $0.00711 avg
  timeout                 20      4235ms avg  $0.00235 avg
  tool_loop               19      6416ms avg  $0.00911 avg
  rate_limit              14       461ms avg  $0.00261 avg
  prompt_injection         6      3135ms avg  $0.00450 avg

by model (llm spans)
  claude-sonnet-4-6        198      4282ms avg  $  3.0785 total
  gpt-4o                   235      4151ms avg  $  2.2338 total
  claude-haiku-4-5         277      2359ms avg  $  1.0159 total
  gpt-4o-mini              412      1876ms avg  $  0.3241 total

cost by release
  2026.8.14       318 traces  $0.00855 avg/trace
  2026.8.19       582 traces  $0.01801 avg/trace
```

Two things planted in that output. Cheaper models are also faster — a real
correlation, not decoration. And release `2026.8.19` costs 2.1x per trace,
because it shifted traffic toward larger models and grew the system prompt.
That is the most common way an AI app's bill doubles between Fridays, and it
gives your cost view something to actually find.

---

## 5. Render a single trace as a waterfall

```bash
python3 harness/explore.py --scenario tool_loop
```

```
trace   e2888e2d0e6941f6a69f6b00
tenant  zephyr   scenario  tool_loop   status  ok
total   4956ms   cost  $0.00246   env  prod   release  2026.8.19
────────────────────────────────────────────────────────────────────────────
▪ support_agent   ██████████████████████████████████████████████    4956ms
  ◆ plan            █                                                 20ms
  ○ embed_query     █                                                 31ms
  ▶ vector_search    ██                                              203ms  k=5 top=0.30
  ● draft_answer      ████████                                       916ms  853→108tok $0.00111
  ▲ policy_check               █                                      24ms  schema_validation=pass
  ■ account_lookup             ████                                  451ms  args=8b0fc20d
  ■ account_lookup                 ████                              393ms  args=8b0fc20d
  ■ account_lookup                     █                              88ms  args=8b0fc20d
  ■ account_lookup                      ██                           244ms  args=8b0fc20d
  ■ account_lookup                        █                           52ms  args=8b0fc20d
  ■ account_lookup                         ██                        243ms  args=8b0fc20d
  ● final_answer                             ████████████████████   2240ms  1164→104tok $0.00135
```

Six identical `args=8b0fc20d` hashes — that's what your agent-loop rule detects.

Other views:

```bash
python3 harness/explore.py                              # list recent traces
python3 harness/explore.py --tenant zephyr
python3 harness/explore.py --scenario retrieval_miss     # shows top=0.21
python3 harness/explore.py --scenario pii_leak --payloads
python3 harness/explore.py 3942b7b2eb0545239eb990c3      # a specific trace
```

`--payloads` prints stored content. Any readable PII there is by definition a
leak — which is exactly what the pii_leak scenario plants for your collector to
catch.

---

## 6. Point it at your collector (once M2 exists)

```bash
python3 harness/run.py --rate 5 --sink http --url http://localhost:8000
```

POSTs `{"spans": [...]}` to `/v1/traces` with `Authorization: Bearer <key>` and
`X-FR-Tenant`. Failures print to stderr rather than being silently dropped.

Verified against a stub collector doing second-pass redaction — 60 traces,
505 spans, 26 payloads caught:

```json
{"traces": 60, "spans": 505, "escaped": 26,
 "findings": {"CREDIT_CARD": 13, "EMAIL": 13, "DOB": 4,
              "PHONE": 2, "PASSPORT": 2, "SIN_CA": 1, "IPV4": 1}}
```

Other sinks: `--sink jsonl` (replay file), `--sink stdout` (one line per trace),
`--sink null` (benchmark), `--sink sqlite,jsonl` (fan out).

---

## 7. Inject one failure on demand, mid-demo

```bash
python3 harness/run.py --count 1 --scenario pii_leak --sink stdout,sqlite
```

```
{"tenant":"northwind","trace":"a91c...","status":"ok","spans":8,"ms":3148,"scenario":"pii_leak"}
```

Wire this to a button in your dashboard header. A judge clicks **PII leak** and
watches the escaped-findings counter tick.

---

## What lands in the database

`flightrecorder.db`, SQLite, two tables.

**spans** — one row per span, indexed on `(tenant_id, start_ns)`:

| column | example |
|---|---|
| tenant_id, trace_id, span_id, parent_span_id | `northwind`, `3942b7b2…` |
| name, kind, status | `vector_search`, `retrieval`, `ok` |
| start_ns, end_ns, duration_ms | `1755…`, `203.4` |
| model, env, release, session_id | `gpt-4o`, `prod`, `2026.8.19` |
| cost_usd, input_tokens, output_tokens | `0.00302`, `2439`, `267` |
| top_score | `0.7249` |
| attrs | full JSON attribute bag |

**payloads** — prompts, completions, retrieved context, tool args. Separate
table on purpose: its own retention clock, its own RBAC capability, and one
`DELETE` reaps it without touching metadata.

Query it directly:

```sql
SELECT trace_id, duration_ms, cost_usd
FROM spans
WHERE tenant_id='northwind' AND parent_span_id IS NULL
ORDER BY start_ns DESC LIMIT 20;
```

Or through the Python API, where `tenant_id` is a required positional argument
on every method:

```python
from fr.store import Store
st = Store("flightrecorder.db")
st.recent_traces("northwind", limit=20)
st.trace("northwind", trace_id)
st.payloads("northwind", trace_id)
st.stats("northwind")
```

---

## Rebuild from scratch

```bash
rm -f flightrecorder.db* traces.jsonl
python3 harness/run.py --backfill 6h --backfill-count 900 --backfill-only
```
