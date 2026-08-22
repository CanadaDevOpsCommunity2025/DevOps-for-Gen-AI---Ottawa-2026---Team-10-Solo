#!/usr/bin/env python3
"""
Terminal trace explorer.

Two jobs. First, it proves the generated data is actually shaped correctly
before you've written a line of UI. Second, it's your fallback demo — if the
dashboard breaks at 2am, an ASCII waterfall in a terminal still tells the story.

    python harness/explore.py                    # list recent traces
    python harness/explore.py --tenant zephyr
    python harness/explore.py <trace_id>         # waterfall for one trace
    python harness/explore.py --scenario pii_leak --payloads
    python harness/explore.py --summary          # fleet-wide rollup
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BAR_W = 46
KIND_GLYPH = {"chain": "▪", "agent": "◆", "retrieval": "▶", "embedding": "○",
              "llm": "●", "tool": "■", "guardrail": "▲"}


def conn(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def list_traces(c, tenant, scenario, limit):
    where, args = ["parent_span_id IS NULL"], []
    if tenant:
        where.append("tenant_id=?"); args.append(tenant)
    if scenario:
        where.append("json_extract(attrs,'$.\"fr.scenario\"')=?"); args.append(scenario)
    args.append(limit)
    rows = c.execute(f"""
        SELECT trace_id, tenant_id, status, duration_ms, cost_usd, model, env, release,
               json_extract(attrs,'$."fr.scenario"') scenario
        FROM spans WHERE {' AND '.join(where)}
        ORDER BY start_ns DESC LIMIT ?""", args).fetchall()

    print(f"{'trace_id':<26}{'tenant':<12}{'scenario':<18}{'':<4}"
          f"{'dur':>9}{'cost':>10}  model")
    print("─" * 100)
    for r in rows:
        flag = "ERR " if r["status"] == "error" else "    "
        print(f"{r['trace_id']:<26}{r['tenant_id']:<12}{r['scenario'] or '-':<18}{flag}"
              f"{r['duration_ms']:>8.0f}ms{r['cost_usd']:>10.5f}  "
              f"{r['model'] or '-'} · {r['env']} · {r['release']}")
    print(f"\n{len(rows)} traces")


def waterfall(c, trace_id, show_payloads):
    rows = c.execute("SELECT * FROM spans WHERE trace_id=? ORDER BY start_ns",
                     (trace_id,)).fetchall()
    if not rows:
        sys.exit(f"no trace {trace_id!r} — run harness/run.py first")

    root = next(r for r in rows if r["parent_span_id"] is None)
    t0, total = root["start_ns"], max(1, root["end_ns"] - root["start_ns"])
    attrs = json.loads(root["attrs"])

    print(f"trace   {trace_id}")
    print(f"tenant  {root['tenant_id']}   scenario  {attrs.get('fr.scenario')}   "
          f"status  {root['status']}")
    print(f"total   {root['duration_ms']:.0f}ms   cost  ${root['cost_usd']:.5f}   "
          f"env  {root['env']}   release  {root['release']}")
    print("─" * 104)

    for r in rows:
        off = (r["start_ns"] - t0) / total
        width = max(1, round((r["end_ns"] - r["start_ns"]) / total * BAR_W))
        pad = round(off * BAR_W)
        if pad + width > BAR_W:
            width = max(1, BAR_W - pad)
        bar = " " * pad + ("░" if r["status"] == "error" else "█") * width
        bar = bar.ljust(BAR_W)

        a = json.loads(r["attrs"])
        note = []
        if r["kind"] == "llm":
            note.append(f"{a.get('gen_ai.usage.input_tokens',0)}→"
                        f"{a.get('gen_ai.usage.output_tokens',0)}tok")
            note.append(f"${r['cost_usd']:.5f}")
        elif r["kind"] == "retrieval":
            note.append(f"k={a.get('fr.retrieval.k')}")
            note.append(f"top={a.get('fr.retrieval.top_score'):.2f}")
        elif r["kind"] == "tool":
            note.append(f"args={a.get('fr.tool.args_hash','')[:8]}")
        elif r["kind"] == "guardrail":
            note.append(f"{a.get('fr.guardrail.rule')}={a.get('fr.guardrail.verdict')}")

        glyph = KIND_GLYPH.get(r["kind"], "·")
        indent = "" if r["parent_span_id"] is None else "  "
        print(f"{indent}{glyph} {r['name']:<16}{bar} {r['duration_ms']:>7.0f}ms  "
              f"{' '.join(note)}")
        if r["error_type"]:
            print(f"      ↳ {r['error_type']}: {r['error_message']}")

    if show_payloads:
        pays = c.execute("SELECT * FROM payloads WHERE trace_id=?", (trace_id,)).fetchall()
        print("\n" + "─" * 104)
        print("payloads (as stored — an unredacted value here is a leak)")
        for p in pays:
            body = p["content"][:220].replace("\n", " ⏎ ")
            print(f"\n  [{p['role']}] {body}{'…' if len(p['content'])>220 else ''}")


def summary(c):
    print("by tenant")
    for r in c.execute("""SELECT tenant_id, COUNT(*) n, SUM(cost_usd) cost,
                          SUM(status='error') errs, AVG(duration_ms) ms
                          FROM spans WHERE parent_span_id IS NULL GROUP BY tenant_id"""):
        print(f"  {r['tenant_id']:<13}{r['n']:>6} traces  ${r['cost']:>9.4f}  "
              f"{r['errs']:>4} err  {r['ms']:>7.0f}ms avg")

    print("\nby scenario")
    for r in c.execute("""SELECT json_extract(attrs,'$."fr.scenario"') s, COUNT(*) n,
                          AVG(duration_ms) ms, AVG(cost_usd) cost
                          FROM spans WHERE parent_span_id IS NULL
                          GROUP BY s ORDER BY n DESC"""):
        print(f"  {r['s'] or '-':<20}{r['n']:>6}  {r['ms']:>8.0f}ms avg  "
              f"${r['cost']:.5f} avg")

    print("\nby model (llm spans)")
    for r in c.execute("""SELECT model, COUNT(*) n, AVG(duration_ms) ms, SUM(cost_usd) cost
                          FROM spans WHERE kind='llm' AND model IS NOT NULL
                          GROUP BY model ORDER BY cost DESC"""):
        print(f"  {r['model']:<22}{r['n']:>6}  {r['ms']:>8.0f}ms avg  ${r['cost']:>8.4f} total")

    print("\ncost by release")
    for r in c.execute("""SELECT release, COUNT(*) n, AVG(cost_usd) avg
                          FROM spans WHERE parent_span_id IS NULL GROUP BY release"""):
        print(f"  {r['release']:<13}{r['n']:>6} traces  ${r['avg']:.5f} avg/trace")


def main():
    p = argparse.ArgumentParser(description="Inspect generated traces")
    p.add_argument("trace_id", nargs="?")
    p.add_argument("--db", default="flightrecorder.db")
    p.add_argument("--tenant")
    p.add_argument("--scenario")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--payloads", action="store_true")
    p.add_argument("--summary", action="store_true")
    a = p.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"{a.db} not found — run: python harness/run.py --backfill 2h")
    c = conn(a.db)

    if a.summary:
        summary(c)
    elif a.trace_id:
        waterfall(c, a.trace_id, a.payloads)
    elif a.scenario:
        row = c.execute("""SELECT trace_id FROM spans WHERE parent_span_id IS NULL
                           AND json_extract(attrs,'$."fr.scenario"')=?
                           ORDER BY start_ns DESC LIMIT 1""", (a.scenario,)).fetchone()
        if not row:
            sys.exit(f"no traces with scenario {a.scenario!r}")
        waterfall(c, row["trace_id"], a.payloads)
    else:
        list_traces(c, a.tenant, a.scenario, a.limit)


if __name__ == "__main__":
    main()
