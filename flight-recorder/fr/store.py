"""
Minimal SQLite store.

Every read and write takes `tenant_id` as a required positional argument. Not a
kwarg, not optional, not derived from a request context somewhere up the stack.
If a query function *can* be called without a tenant, someone will eventually
call it without a tenant — usually at 2am, in the one code path nobody reviewed.

Swap for ClickHouse when the hackathon is over; the interface is deliberately
narrow enough that it's a one-file change.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Iterable

from fr.schema import Span

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
  tenant_id      TEXT NOT NULL,
  trace_id       TEXT NOT NULL,
  span_id        TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL,
  error_type     TEXT,
  error_message  TEXT,
  start_ns       INTEGER NOT NULL,
  end_ns         INTEGER NOT NULL,
  duration_ms    REAL NOT NULL,
  model          TEXT,
  env            TEXT,
  release        TEXT,
  session_id     TEXT,
  cost_usd       REAL DEFAULT 0,
  input_tokens   INTEGER DEFAULT 0,
  output_tokens  INTEGER DEFAULT 0,
  top_score      REAL,
  attrs          TEXT NOT NULL,
  PRIMARY KEY (tenant_id, span_id)
);
CREATE INDEX IF NOT EXISTS ix_trace  ON spans(tenant_id, trace_id);
CREATE INDEX IF NOT EXISTS ix_time   ON spans(tenant_id, start_ns DESC);
CREATE INDEX IF NOT EXISTS ix_kind   ON spans(tenant_id, kind, start_ns DESC);

-- Payloads live in their own table: separate retention clock, separate RBAC
-- capability, and a single DELETE reaps them without touching metadata.
CREATE TABLE IF NOT EXISTS payloads (
  tenant_id  TEXT NOT NULL,
  trace_id   TEXT NOT NULL,
  span_id    TEXT NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  report     TEXT NOT NULL,
  created_ns INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pay ON payloads(tenant_id, trace_id);
"""


class Store:
    def __init__(self, path: str = "flightrecorder.db"):
        self.path = path
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.row_factory = sqlite3.Row
            self._local.conn = c
        return c

    # -- write ---------------------------------------------------------------
    def write_spans(self, tenant_id: str, spans: Iterable[Span]) -> int:
        rows, prows = [], []
        for s in spans:
            if s.tenant_id != tenant_id:
                raise ValueError(f"span tenant {s.tenant_id!r} != {tenant_id!r}")
            a = s.attrs
            rows.append((
                tenant_id, s.trace_id, s.span_id, s.parent_span_id, s.name,
                s.kind, s.status, s.error_type, s.error_message,
                s.start_ns, s.end_ns, s.duration_ms,
                a.get("gen_ai.request.model") or a.get("fr.trace.model"),
                a.get("fr.env"),
                a.get("fr.release"), a.get("fr.session.id"),
                float(a.get("fr.cost.usd", 0) or 0),
                int(a.get("gen_ai.usage.input_tokens", 0) or 0),
                int(a.get("gen_ai.usage.output_tokens", 0) or 0),
                a.get("fr.retrieval.top_score"),
                json.dumps(a, separators=(",", ":")),
            ))
            for p in s.payloads:
                prows.append((tenant_id, p.trace_id, p.span_id, p.role,
                              p.content, json.dumps(p.redaction_report),
                              p.created_ns))
        c = self._conn()
        with c:
            c.executemany(
                "INSERT OR REPLACE INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            if prows:
                c.executemany("INSERT INTO payloads VALUES (?,?,?,?,?,?,?)", prows)
        return len(rows)

    # -- read ----------------------------------------------------------------
    def recent_traces(self, tenant_id: str, limit: int = 50) -> list[dict]:
        c = self._conn()
        q = """
        SELECT trace_id, name, status, start_ns, duration_ms, model, env, release,
               cost_usd, json_extract(attrs,'$."fr.scenario"') AS scenario,
               json_extract(attrs,'$."fr.span_count"') AS span_count
        FROM spans
        WHERE tenant_id=? AND parent_span_id IS NULL
        ORDER BY start_ns DESC LIMIT ?"""
        return [dict(r) for r in c.execute(q, (tenant_id, limit))]

    def trace(self, tenant_id: str, trace_id: str) -> list[dict]:
        c = self._conn()
        rows = c.execute(
            "SELECT * FROM spans WHERE tenant_id=? AND trace_id=? ORDER BY start_ns",
            (tenant_id, trace_id))
        out = []
        for r in rows:
            d = dict(r)
            d["attrs"] = json.loads(d["attrs"])
            out.append(d)
        return out

    def payloads(self, tenant_id: str, trace_id: str) -> list[dict]:
        c = self._conn()
        return [dict(r) for r in c.execute(
            "SELECT * FROM payloads WHERE tenant_id=? AND trace_id=?",
            (tenant_id, trace_id))]

    def stats(self, tenant_id: str) -> dict:
        c = self._conn()
        r = c.execute("""
          SELECT COUNT(*) n, SUM(cost_usd) cost,
                 SUM(status='error') errs, AVG(duration_ms) avg_ms
          FROM spans WHERE tenant_id=? AND parent_span_id IS NULL
        """, (tenant_id,)).fetchone()
        return {"traces": r["n"], "cost_usd": round(r["cost"] or 0, 4),
                "errors": r["errs"] or 0, "avg_ms": round(r["avg_ms"] or 0, 1)}

    def escaped_traces(self, tenant_id: str) -> int:
        """
        Traces where the collector's second-pass scan caught PII the SDK missed.
        One SQL query rather than a fetch-per-trace loop — this number sits on
        the dashboard and gets recomputed on every poll.
        """
        return self._conn().execute(
            "SELECT COUNT(DISTINCT trace_id) FROM spans "
            "WHERE tenant_id=? AND json_extract(attrs,'$.\"fr.redaction.escaped\"')=1",
            (tenant_id,)).fetchone()[0]

    def tenants(self) -> list[str]:
        return [r[0] for r in self._conn().execute(
            "SELECT DISTINCT tenant_id FROM spans")]

    def count(self) -> int:
        return self._conn().execute(
            "SELECT COUNT(*) FROM spans WHERE parent_span_id IS NULL").fetchone()[0]
