"""
Sinks.

The generator must not care where traces go. Today it's SQLite because your
collector doesn't exist yet; in two hours it's an HTTP POST to /v1/traces and
nothing in agent.py changes.

The `http` sink is also how you prove the collector's second-pass redaction
works: it ships payloads *unredacted* on purpose under the pii_leak scenario, so
a correct collector flags `fr.redaction.escaped` on arrival.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from typing import Protocol

from fr import rca
from fr.redaction import Redactor
from fr.schema import Span
from fr.store import Store


class Sink(Protocol):
    def emit(self, tenant_id: str, spans: list[Span]) -> None: ...
    def close(self) -> None: ...


class SqliteSink:
    """
    Acts as an in-process collector: runs the zero-trust second-pass scan before
    anything touches disk. Without this the sink is a hole in the design — the
    HTTP path would redact and the local path wouldn't, and the database would
    quietly accumulate exactly the raw PII the whole project claims to prevent.

    Set redact=False only to demonstrate what an unprotected pipeline looks like.
    """

    def __init__(self, path: str = "flightrecorder.db", redact: bool = True):
        self.store = Store(path)
        self.redact = redact
        self.n = 0
        self.escaped = 0
        self._lock = threading.Lock()
        self._redactors: dict[str, Redactor] = {}

    def _redactor(self, tenant_id: str) -> Redactor:
        r = self._redactors.get(tenant_id)
        if r is None:
            # per-tenant secret: same value yields different tokens per tenant
            r = Redactor(hashlib.sha256(f"fr::{tenant_id}".encode()).digest())
            self._redactors[tenant_id] = r
        return r

    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        if self.redact:
            r = self._redactor(tenant_id)
            for sp in spans:
                findings: dict[str, int] = {}
                for p in sp.payloads:
                    safe, rep = r.redact(p.content)
                    if rep["count"]:
                        p.content = safe
                        p.redaction_report = rep
                        for k, v in rep["findings"].items():
                            findings[k] = findings.get(k, 0) + v
                if findings:
                    # the SDK was supposed to catch this in-process; it didn't
                    sp.attrs["fr.redaction.escaped"] = True
                    sp.attrs["fr.redaction.findings"] = findings
                    with self._lock:
                        self.escaped += 1
        self.store.write_spans(tenant_id, spans)
        with self._lock:
            self.n += 1

    def close(self) -> None:
        if self.escaped:
            print(f"[collector] second-pass scan redacted {self.escaped} span(s)",
                  file=sys.stderr)


class JsonlSink:
    """One JSON object per trace. Good for replay and for diffing runs."""

    def __init__(self, path: str = "traces.jsonl"):
        self.f = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        rec = {"tenant_id": tenant_id,
               "trace_id": spans[0].trace_id,
               "spans": [s.to_dict() for s in spans]}
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with self._lock:
            self.f.write(line + "\n")

    def close(self) -> None:
        self.f.close()


class HttpSink:
    """
    POSTs to your collector. Falls back loudly rather than silently dropping —
    a generator that quietly discards traces will cost you an hour of debugging
    the wrong layer.
    """

    def __init__(self, url: str, api_keys: dict[str, str] | None = None,
                 timeout: float = 5.0):
        import urllib.request  # stdlib only, no dependency on requests
        self._urllib = urllib.request
        self.url = url.rstrip("/")
        self.api_keys = api_keys or {}
        self.timeout = timeout
        self.failures = 0

    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        body = json.dumps(
            {"spans": [s.to_dict() for s in spans]},
            separators=(",", ":"), default=str).encode()
        req = self._urllib.Request(
            f"{self.url}/v1/traces", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_keys.get(tenant_id, 'dev')}",
                     "X-FR-Tenant": tenant_id})
        try:
            with self._urllib.urlopen(req, timeout=self.timeout) as r:
                r.read()
        except Exception as e:
            self.failures += 1
            if self.failures <= 3:
                print(f"[sink] POST failed: {e}", file=sys.stderr)
            elif self.failures == 4:
                print("[sink] suppressing further POST errors", file=sys.stderr)

    def close(self) -> None:
        if self.failures:
            print(f"[sink] {self.failures} failed POSTs", file=sys.stderr)


class SentinelSink:
    """
    Forwards traces to the Sentinel observability platform
    (../platform) as a real monitored application, keyed by
    sentinel-config.json (see register_with_sentinel.py).

    Two dashboards, two jobs: flight-recorder's own console (server.py/ui/)
    stays the place to generate and eyeball raw RAG traces span-by-span.
    This sink is the bridge that also lets those same traces show up in
    Sentinel as PII-redaction events, undeclared-PII drift alerts, and
    RCA-verdict-driven audit events/alerts — reusing fr.rca's rules engine
    for the verdict rather than re-deriving one.

    Payload text is defensively re-redacted here (not just relying on an
    earlier sink in a `--sink sqlite,sentinel` fanout) so nothing unmasked
    ever crosses the HTTP call, regardless of sink order.
    """

    SEVERITY_MAP = {"critical": "critical", "warning": "high", "info": "info"}
    COST_BASELINE_USD = 0.01  # rough tenant cost_p50 stand-in; RCA needs *some* baseline

    def __init__(self, url: str = "http://localhost:4000", config_path: str = "sentinel-config.json"):
        self.url = url.rstrip("/")
        self.config_path = config_path
        self.config: dict[str, dict] = {}
        self._warned_missing = False
        self._redactors: dict[str, Redactor] = {}
        self._lock = threading.Lock()
        self.posted = 0
        self.audits = 0
        self.failures = 0
        if os.path.exists(config_path):
            with open(config_path) as f:
                self.config = json.load(f)
        else:
            print(f"[sentinel] {config_path} not found — run register_with_sentinel.py first. "
                  f"Traces will be generated but not forwarded to Sentinel.", file=sys.stderr)

    def _redactor(self, tenant_id: str) -> Redactor:
        r = self._redactors.get(tenant_id)
        if r is None:
            r = Redactor(hashlib.sha256(f"fr::{tenant_id}".encode()).digest())
            self._redactors[tenant_id] = r
        return r

    def _post(self, path: str, app_cfg: dict, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        req = urllib.request.Request(
            f"{self.url}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "x-app-id": app_cfg["appId"],
                     "x-ingest-key": app_cfg["ingestKey"]})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                r.read()
        except Exception as e:
            with self._lock:
                self.failures += 1
                n = self.failures
            if n <= 3:
                print(f"[sentinel] POST {path} failed: {e}", file=sys.stderr)

    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        app_cfg = self.config.get(tenant_id)
        if not app_cfg:
            if not self._warned_missing:
                print(f"[sentinel] no registered app for tenant {tenant_id!r} in "
                      f"{self.config_path} — skipping", file=sys.stderr)
                self._warned_missing = True
            return

        redactor = self._redactor(tenant_id)
        for sp in spans:
            for p in sp.payloads:
                if not p.redaction_report:
                    safe, rep = redactor.redact(p.content)
                    if rep["count"]:
                        p.content = safe
                        p.redaction_report = rep
                findings = (p.redaction_report or {}).get("findings", {})
                entities = [{"type": t} for t, n in findings.items() for _ in range(n)]
                self._post("/api/ingest/telemetry", app_cfg, {
                    "field": f"{sp.kind}.{p.role}",
                    "redactedText": p.content,
                    "entities": entities,
                })
                with self._lock:
                    self.posted += 1

        verdict = rca.analyse([s.to_dict() for s in spans], ctx={"cost_p50": self.COST_BASELINE_USD})
        if verdict and verdict["code"] != "nominal":
            self._post("/api/ingest/audit", app_cfg, {
                "eventType": verdict["code"],
                "severity": self.SEVERITY_MAP.get(verdict["severity"], "warn"),
                "detail": f"{verdict['title']} — {verdict['detail']}",
                "actor": "flight-recorder-rca",
            })
            with self._lock:
                self.audits += 1

    def close(self) -> None:
        print(f"[sentinel] {self.posted} telemetry event(s), {self.audits} audit event(s), "
              f"{self.failures} failed POST(s)", file=sys.stderr)


class StdoutSink:
    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        root = spans[0]
        print(json.dumps({"tenant": tenant_id, "trace": root.trace_id,
                          "status": root.status, "spans": len(spans),
                          "ms": round(root.duration_ms, 1),
                          "scenario": root.attrs.get("fr.scenario")},
                         separators=(",", ":")))

    def close(self) -> None:
        pass


class NullSink:
    """Throughput benchmarking without I/O in the way."""
    def emit(self, tenant_id: str, spans: list[Span]) -> None: pass
    def close(self) -> None: pass


class FanoutSink:
    def __init__(self, *sinks: Sink):
        self.sinks = sinks

    def emit(self, tenant_id: str, spans: list[Span]) -> None:
        for s in self.sinks:
            s.emit(tenant_id, spans)

    def close(self) -> None:
        for s in self.sinks:
            s.close()


def build(spec: str, db: str, url: str,
          sentinel_url: str = "http://localhost:4000",
          sentinel_config: str = "sentinel-config.json") -> Sink:
    parts = [p.strip() for p in spec.split(",") if p.strip()]
    made = []
    for p in parts:
        if p == "sqlite":
            made.append(SqliteSink(db))
        elif p == "sqlite-raw":
            made.append(SqliteSink(db, redact=False))
        elif p == "http":
            made.append(HttpSink(url))
        elif p == "sentinel":
            made.append(SentinelSink(sentinel_url, sentinel_config))
        elif p == "jsonl":
            made.append(JsonlSink("traces.jsonl"))
        elif p == "stdout":
            made.append(StdoutSink())
        elif p == "null":
            made.append(NullSink())
        else:
            raise SystemExit(f"unknown sink {p!r}: use sqlite|sqlite-raw|http|sentinel|jsonl|stdout|null")
    return made[0] if len(made) == 1 else FanoutSink(*made)
