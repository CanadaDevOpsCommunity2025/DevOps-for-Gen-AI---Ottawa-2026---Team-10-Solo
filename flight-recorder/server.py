#!/usr/bin/env python3
"""
Flight Recorder console server.

    python3 server.py            # http://localhost:8000

Reads the SQLite database the generator writes. Endpoints are deliberately thin
— all the judgement lives in fr/rca.py and fr/governance.py — so the UI stays a
rendering layer and the interesting logic stays testable without a browser.

Role is passed as a header (X-FR-Role) purely so the demo can switch it from a
dropdown. In production this comes from the API key, and `fr/governance.py`
already has that path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import hashlib
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fr.store import Store            # noqa: E402
from fr.rca import analyse, healthy   # noqa: E402
from fr.redaction import Redactor     # noqa: E402
from fr.governance import ROLES, CAP_TRACE_READ, CAP_PAYLOAD_READ, CAP_UNMASK  # noqa: E402

DB = os.environ.get("FR_DB", "flightrecorder.db")
ROOT = Path(__file__).parent
api = FastAPI(title="AI Application Flight Recorder")
store = Store(DB)

# In-memory audit chain. Hash-linked so tampering is detectable; swap for an
# append-only table when this outlives the hackathon.
AUDIT: list[dict] = []


def audit(action: str, principal: str, tenant: str, target: str, allowed: bool):
    prev = AUDIT[-1]["hash"] if AUDIT else "genesis"
    entry = {"seq": len(AUDIT) + 1, "ts": time.time(), "action": action,
             "principal": principal, "tenant": tenant, "target": target,
             "allowed": allowed, "prev": prev}
    body = json.dumps({k: entry[k] for k in sorted(entry)}, separators=(",", ":"))
    entry["hash"] = hashlib.sha256((prev + body).encode()).hexdigest()[:16]
    AUDIT.append(entry)
    return entry


def caps(role: str) -> set:
    return ROLES.get(role, set())


def require(role: str, cap: str, action: str, tenant: str, target: str = "-"):
    ok = cap in caps(role)
    audit(action, role, tenant, target, ok)
    if not ok:
        raise HTTPException(403, f"role '{role}' lacks {cap}")


def _p50_cost(tenant: str) -> float:
    rows = sorted(t["cost_usd"] or 0 for t in store.recent_traces(tenant, 300))
    return rows[len(rows) // 2] if rows else 0.0


# --------------------------------------------------------------------- routes
@api.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "ui" / "index.html").read_text(encoding="utf-8")


@api.get("/api/tenants")
def tenants():
    return {"tenants": store.tenants()}


@api.get("/api/stats")
def stats(tenant: str = Query(...), x_fr_role: str = Header("developer")):
    require(x_fr_role, CAP_TRACE_READ, "stats.read", tenant)
    s = store.stats(tenant)
    recent = store.recent_traces(tenant, 400)
    escaped = store.escaped_traces(tenant)
    by_release: dict[str, list] = {}
    for t in recent:
        by_release.setdefault(t["release"] or "-", []).append(t["cost_usd"] or 0)
    s["escaped"] = escaped
    s["p50_cost"] = round(_p50_cost(tenant), 5)
    s["releases"] = {k: {"n": len(v), "avg": round(sum(v) / max(len(v), 1), 5)}
                     for k, v in sorted(by_release.items())}
    return s


@api.get("/api/traces")
def traces(tenant: str = Query(...), limit: int = 60,
           x_fr_role: str = Header("developer")):
    require(x_fr_role, CAP_TRACE_READ, "traces.list", tenant)
    return {"traces": store.recent_traces(tenant, limit)}


@api.get("/api/trace/{trace_id}")
def trace(trace_id: str, tenant: str = Query(...),
          x_fr_role: str = Header("developer")):
    require(x_fr_role, CAP_TRACE_READ, "trace.read", tenant, trace_id)
    spans = store.trace(tenant, trace_id)
    if not spans:
        # 404 not 403 — a 403 would confirm the trace exists in another tenant
        raise HTTPException(404, "no such trace")
    v = analyse(spans, {"cost_p50": _p50_cost(tenant)}) or healthy()
    return {"spans": spans, "verdict": v}


@api.get("/api/payloads/{trace_id}")
def payloads(trace_id: str, tenant: str = Query(...),
             x_fr_role: str = Header("developer")):
    require(x_fr_role, CAP_PAYLOAD_READ, "payload.read", tenant, trace_id)
    rows = store.payloads(tenant, trace_id)
    # Defence in depth: mask on the way out too. If anything slipped past
    # ingest, it still never reaches a browser.
    r = Redactor(hashlib.sha256(tenant.encode()).digest())
    out = []
    for p in rows:
        safe, rep = r.redact(p["content"])
        # What ingest caught, recorded when the span was written. Read-time
        # findings are normally empty precisely *because* ingest did its job —
        # so show the ingest report, and flag read-time hits separately as a
        # defence-in-depth catch that shouldn't have been needed.
        try:
            ingest = json.loads(p["report"] or "{}").get("findings", {})
        except (ValueError, TypeError):
            ingest = {}
        out.append({"role": p["role"], "span_id": p["span_id"],
                    "content": safe,
                    "findings": ingest or rep["findings"],
                    "caught_at": "ingest" if ingest else ("read" if rep["count"] else None),
                    "masked_here": bool(rep["count"])})
    return {"payloads": out, "can_unmask": CAP_UNMASK in caps(x_fr_role)}


@api.get("/api/audit")
def audit_log(x_fr_role: str = Header("developer"), limit: int = 40):
    if "audit:read" not in caps(x_fr_role) and "tenant:admin" not in caps(x_fr_role):
        # every role can see its own trail length, nobody sees content without the cap
        return {"entries": [], "total": len(AUDIT), "locked": True,
                "verified": verify_chain()}
    return {"entries": AUDIT[-limit:][::-1], "total": len(AUDIT),
            "locked": False, "verified": verify_chain()}


def verify_chain() -> bool:
    prev = "genesis"
    for e in AUDIT:
        body = json.dumps({k: e[k] for k in sorted(e) if k != "hash"},
                          separators=(",", ":"))
        if hashlib.sha256((prev + body).encode()).hexdigest()[:16] != e["hash"]:
            return False
        prev = e["hash"]
    return True


@api.post("/api/inject")
def inject(scenario: str = Query(...), count: int = 1):
    """
    Fire a scenario on demand. This is the button a judge clicks.

    Sinks to both sqlite (this console) and sentinel — otherwise traffic
    generated interactively here would only ever show up in this UI, and
    Sentinel would only ever reflect whatever was pushed at deploy time.
    Missing sentinel-config.json degrades gracefully: SentinelSink just
    warns and skips the forward, so the sqlite side still works.
    """
    from harness.scenarios import SCENARIOS
    if scenario not in SCENARIOS:
        raise HTTPException(400, f"unknown scenario {scenario}")
    cmd = [sys.executable, str(ROOT / "harness" / "run.py"),
           "--count", str(count), "--scenario", scenario,
           "--sink", "sqlite,sentinel", "--db", DB, "--rate", "50"]
    subprocess.run(cmd, capture_output=True, timeout=60, cwd=ROOT)
    return {"ok": True, "scenario": scenario, "count": count}


@api.get("/api/roles")
def roles():
    return {"roles": {r: sorted(c) for r, c in ROLES.items()}}


@api.exception_handler(HTTPException)
def http_err(request, exc):
    return JSONResponse({"error": exc.detail, "status": exc.status_code},
                        status_code=exc.status_code)


if __name__ == "__main__":
    import uvicorn
    if not os.path.exists(DB):
        print(f"! {DB} not found. Run:\n"
              f"  python3 harness/run.py --backfill 6h --backfill-count 900 "
              f"--backfill-only\n", file=sys.stderr)
    print("console  →  http://localhost:8000")
    uvicorn.run(api, host="0.0.0.0", port=8000, log_level="warning")
