#!/usr/bin/env python3
"""
Traffic generator for the AI Application Flight Recorder.

    # fill an empty DB with 6 hours of realistic history, instantly
    python harness/run.py --backfill 6h --backfill-count 900

    # then stream live traffic on top of it
    python harness/run.py --rate 3 --scenario mixed --duration 60

    # every failure mode inside 20 traces, for rehearsing the demo
    python harness/run.py --rate 2 --scenario demo

    # one specific failure, on demand, mid-demo
    python harness/run.py --count 1 --scenario pii_leak --sink stdout

    # point at your collector once it exists
    python harness/run.py --rate 5 --sink http --url http://localhost:8000
"""
from __future__ import annotations

import argparse
import os
import random
import re
import signal
import sys
import threading
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness import corpus as C            # noqa: E402
from harness import distributions as D     # noqa: E402
from harness import sinks as S             # noqa: E402
from harness.agent import generate_trace   # noqa: E402
from harness.scenarios import SCENARIOS, resolve, weights_for  # noqa: E402

STOP = threading.Event()
DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$")
_MULT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(s: str) -> float:
    m = DUR_RE.match(s.strip().lower())
    if not m:
        raise SystemExit(f"bad duration {s!r} — use 90s, 5m, 6h, 2d")
    return float(m.group(1)) * _MULT[m.group(2)]


def pick_scenario(rnd: random.Random, name: str):
    fixed = resolve(name)
    if fixed:
        return fixed
    w = weights_for(name)
    return SCENARIOS[rnd.choices(list(w), weights=list(w.values()), k=1)[0]]


# --------------------------------------------------------------------------
def backfill(args, sink, rnd: random.Random) -> Counter:
    """
    Generate history in the past, weighted by a diurnal curve so the volume
    chart has a shape the moment the UI loads. This is the single highest-value
    twenty lines in the file — building a dashboard against an empty table is
    how teams lose their afternoon.
    """
    window_s = parse_duration(args.backfill)
    n = args.backfill_count
    now = time.time()
    start = now - window_s

    # sample timestamps proportional to the diurnal weight of their hour
    buckets = 96
    edges = [start + i * window_s / buckets for i in range(buckets)]
    weights = [D.diurnal_weight(time.localtime(e).tm_hour +
                                time.localtime(e).tm_min / 60.0) for e in edges]

    counts = Counter()
    t0 = time.time()
    for i in range(n):
        b = rnd.choices(range(buckets), weights=weights, k=1)[0]
        ts = edges[b] + rnd.uniform(0, window_s / buckets)
        sc = pick_scenario(rnd, args.scenario)
        spans, meta = generate_trace(rnd, sc, int(ts * 1e9))
        sink.emit(meta["tenant_id"], spans)
        counts[sc.key] += 1
        if args.progress and (i + 1) % 100 == 0:
            print(f"  backfilled {i+1}/{n}", file=sys.stderr)

    el = time.time() - t0
    print(f"[backfill] {n} traces across {args.backfill} "
          f"in {el:.1f}s ({n/max(el,0.01):.0f}/s)", file=sys.stderr)
    return counts


# --------------------------------------------------------------------------
def stream(args, sink, rnd: random.Random) -> Counter:
    """Live pacing. Rate is modulated by the diurnal curve unless --flat."""
    counts = Counter()
    emitted = 0
    t0 = time.time()
    next_at = t0
    last_report = t0

    while not STOP.is_set():
        if args.count and emitted >= args.count:
            break
        if args.duration and (time.time() - t0) >= args.duration:
            break

        now = time.time()
        if now < next_at:
            time.sleep(min(0.02, next_at - now))
            continue

        sc = pick_scenario(rnd, args.scenario)
        spans, meta = generate_trace(rnd, sc, time.time_ns())
        sink.emit(meta["tenant_id"], spans)
        counts[sc.key] += 1
        emitted += 1

        if args.verbose:
            flag = "ERR" if meta["status"] == "error" else "ok "
            print(f"{flag} {meta['tenant_id']:<11} {meta['scenario']:<17} "
                  f"{meta['duration_ms']:>8.0f}ms  ${meta['cost_usd']:.5f}  "
                  f"top={meta['top_score']:.2f}  {meta['trace_id'][:12]}",
                  file=sys.stderr)

        # pace to the target rate; Poisson gaps look like real arrivals
        rate = args.rate
        if not args.flat:
            lt = time.localtime()
            rate *= D.diurnal_weight(lt.tm_hour + lt.tm_min / 60.0)
        rate = max(0.05, rate)
        next_at += rnd.expovariate(rate) if args.poisson else (1.0 / rate)

        if not args.verbose and time.time() - last_report >= 5:
            el = time.time() - t0
            print(f"[stream] {emitted} traces  {emitted/el:.1f}/s  "
                  f"errors={sum(v for k,v in counts.items() if k!='nominal')}",
                  file=sys.stderr)
            last_report = time.time()

    return counts


# --------------------------------------------------------------------------
def selfcheck(rnd: random.Random) -> int:
    """
    Emits one trace per scenario and asserts the deformation actually took.
    Run this before the demo. If a scenario silently stops deforming — easy to
    do while refactoring — you find out here, not on stage.
    """
    from fr.schema import A_TOP_SCORE, A_TOOL_ARGS_HASH, A_IN_TOK
    fails = 0
    print(f"{'scenario':<18} {'spans':>5} {'ms':>8} {'cost':>9}  check")
    print("-" * 66)
    for key, sc in SCENARIOS.items():
        spans, meta = generate_trace(rnd, sc, time.time_ns())
        ok, note = True, ""

        if key == "retrieval_miss":
            top = next(s.attrs[A_TOP_SCORE] for s in spans if s.kind == "retrieval")
            ok = top <= 0.25
            note = f"top_score={top:.2f} (want <=0.25)"
        elif key == "tool_loop":
            hashes = [s.attrs[A_TOOL_ARGS_HASH] for s in spans if s.kind == "tool"]
            ok = len(hashes) >= 6 and len(set(hashes)) == 1
            note = f"{len(hashes)} tool spans, {len(set(hashes))} distinct args"
        elif key == "cost_spike":
            base = 0.004
            ok = meta["cost_usd"] > base
            note = f"cost=${meta['cost_usd']:.4f} (want > ${base})"
        elif key == "pii_leak":
            blob = " ".join(p.content for s in spans for p in s.payloads)
            ok = "@" in blob and "4532015112830366" in blob
            note = "raw PII present for collector to catch"
        elif key in ("timeout", "rate_limit"):
            ok = meta["status"] == "error"
            note = f"status={meta['status']}"
        elif key == "prompt_injection":
            v = next((s.attrs.get("fr.guardrail.verdict") for s in spans
                      if s.kind == "guardrail"), None)
            ok = v == "block"
            note = f"guardrail={v}"
        elif key == "latency_spike":
            longest = max(s.duration_ms for s in spans if s.parent_span_id)
            ok = longest / meta["duration_ms"] > 0.55
            note = f"bottleneck={longest/meta['duration_ms']:.0%} of trace"
        else:
            note = "shape only"

        fails += not ok
        print(f"{key:<18} {meta['span_count']:>5} {meta['duration_ms']:>8.0f} "
              f"{meta['cost_usd']:>9.5f}  {'PASS' if ok else 'FAIL'}  {note}")

    print("-" * 66)
    print("all scenarios deform correctly" if not fails else f"{fails} FAILED")
    return 1 if fails else 0


# --------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        description="Synthetic AI-app traffic for the flight recorder",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--rate", type=float, default=3.0, help="traces/sec (default 3)")
    p.add_argument("--scenario", default="mixed",
                   help="mixed | demo | " + " | ".join(sorted(SCENARIOS)))
    p.add_argument("--duration", type=float, default=0, help="seconds; 0 = forever")
    p.add_argument("--count", type=int, default=0, help="stop after N traces")
    p.add_argument("--sink", default="sqlite", help="sqlite,http,sentinel,jsonl,stdout,null")
    p.add_argument("--db", default="flightrecorder.db")
    p.add_argument("--url", default="http://localhost:8000", help="collector URL for the http sink")
    p.add_argument("--sentinel-url", default="http://localhost:4000",
                   help="Sentinel platform URL for the sentinel sink")
    p.add_argument("--sentinel-config", default="sentinel-config.json",
                   help="output of register_with_sentinel.py")
    p.add_argument("--seed", type=int, default=None, help="reproduce a run exactly")
    p.add_argument("--backfill", default=None, help="e.g. 6h — generate history first")
    p.add_argument("--backfill-count", type=int, default=600)
    p.add_argument("--backfill-only", action="store_true",
                   help="generate history then exit, no live stream")
    p.add_argument("--workers", type=int, default=1, help="parallel emitters")
    p.add_argument("--poisson", action="store_true", help="Poisson arrivals")
    p.add_argument("--flat", action="store_true", help="disable diurnal modulation")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--progress", action="store_true")
    p.add_argument("--selfcheck", action="store_true",
                   help="emit one of each scenario and assert the deformations")
    args = p.parse_args()

    rnd = random.Random(args.seed)
    if args.selfcheck:
        sys.exit(selfcheck(rnd))

    sink = S.build(args.sink, args.db, args.url, args.sentinel_url, args.sentinel_config)
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    counts = Counter()
    t0 = time.time()

    if args.backfill:
        counts += backfill(args, sink, rnd)

    if args.rate > 0 and not args.backfill_only:
        if args.workers > 1:
            per = argparse.Namespace(**vars(args))
            per.rate = args.rate / args.workers
            lock = threading.Lock()
            results = []

            def worker(seed):
                r = random.Random(seed)
                c = stream(per, sink, r)
                with lock:
                    results.append(c)

            threads = [threading.Thread(target=worker, args=(rnd.random(),), daemon=True)
                       for _ in range(args.workers)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for c in results:
                counts += c
        else:
            counts += stream(args, sink, rnd)

    sink.close()
    total = sum(counts.values())
    el = time.time() - t0
    print(f"\n[done] {total} traces in {el:.1f}s ({total/max(el,0.01):.1f}/s)",
          file=sys.stderr)
    for k, v in counts.most_common():
        print(f"  {k:<18} {v:>5}  {v/max(total,1):>5.1%}", file=sys.stderr)

    if "sqlite" in args.sink:
        from fr.store import Store
        st = Store(args.db)
        print(f"\n[db] {args.db} — {st.count()} traces across "
              f"{len(st.tenants())} tenants", file=sys.stderr)
        for t in st.tenants():
            s = st.stats(t)
            print(f"  {t:<12} {s['traces']:>5} traces  ${s['cost_usd']:>8.4f}  "
                  f"{s['errors']:>3} errors  {s['avg_ms']:>7.0f}ms avg", file=sys.stderr)


if __name__ == "__main__":
    main()
