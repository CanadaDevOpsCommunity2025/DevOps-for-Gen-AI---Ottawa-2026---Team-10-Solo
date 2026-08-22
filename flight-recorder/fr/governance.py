"""
Tenants, keys, roles, retention. Everything that decides *who may see what* and
*for how long* lives here so there is exactly one place to audit.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field

# --- capabilities -----------------------------------------------------------
# Deliberately finer-grained than "read". Seeing that a trace failed and seeing
# the prompt that failed are different privileges.
CAP_TRACE_READ = "trace:read"        # metadata, timings, costs, scores
CAP_PAYLOAD_READ = "payload:read"    # redacted prompts / completions / chunks
CAP_UNMASK = "payload:unmask"        # break-glass: request re-identification
CAP_INGEST = "trace:write"
CAP_AUDIT_READ = "audit:read"
CAP_ADMIN = "tenant:admin"

ROLES = {
    # on-call engineer: can see everything went wrong, cannot read user content
    "responder": {CAP_TRACE_READ},
    # app developer: can read redacted content to actually debug a prompt
    "developer": {CAP_TRACE_READ, CAP_PAYLOAD_READ},
    # compliance: can read the audit chain, cannot read traces
    "auditor":   {CAP_AUDIT_READ},
    # security lead: break-glass unmask, always audited
    "security":  {CAP_TRACE_READ, CAP_PAYLOAD_READ, CAP_UNMASK, CAP_AUDIT_READ},
    "admin":     {CAP_TRACE_READ, CAP_PAYLOAD_READ, CAP_UNMASK,
                  CAP_AUDIT_READ, CAP_ADMIN, CAP_INGEST},
    # the SDK's own key: write-only. A leaked ingest key cannot read anything.
    "agent":     {CAP_INGEST},
}


@dataclass
class Policy:
    """Per-tenant governance configuration."""
    redaction_mode: str = "mask"           # off | mask | hash | drop
    allow_labels: set[str] = field(default_factory=set)
    retain_metadata_days: int = 90
    retain_payload_days: int = 7           # payloads die first, always
    retain_audit_days: int = 365
    sample_rate: float = 1.0               # head sampling
    always_sample_errors: bool = True      # tail rule: never drop a failure
    max_payload_chars: int = 8000
    residency: str = "ca-central-1"        # informational; enforce at deploy
    daily_cost_ceiling_usd: float = 50.0


@dataclass
class Tenant:
    tenant_id: str
    name: str
    policy: Policy = field(default_factory=Policy)
    # per-tenant salt. Rotating it severs all pseudonym linkage for that tenant
    # without touching anyone else's data — that's the erasure lever.
    secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))


@dataclass
class Principal:
    """Whoever is making the request — a human or the SDK."""
    principal_id: str
    tenant_id: str
    role: str

    @property
    def caps(self) -> set[str]:
        return ROLES.get(self.role, set())

    def can(self, cap: str) -> bool:
        return cap in self.caps


class KeyRing:
    """
    API keys are stored hashed, never in the clear. Format:
        fr_<role>_<tenant>_<random>
    The prefix is informational for humans grepping logs; authority comes from
    the stored record, not the string.
    """

    def __init__(self, pepper: bytes | None = None):
        self.pepper = pepper or os.urandom(32)
        self._keys: dict[str, Principal] = {}
        self.tenants: dict[str, Tenant] = {}

    def _h(self, key: str) -> str:
        return hmac.new(self.pepper, key.encode(), hashlib.sha256).hexdigest()

    def add_tenant(self, tenant_id: str, name: str, policy: Policy | None = None) -> Tenant:
        t = Tenant(tenant_id, name, policy or Policy())
        self.tenants[tenant_id] = t
        return t

    def issue(self, tenant_id: str, role: str, principal_id: str) -> str:
        assert role in ROLES, f"unknown role {role}"
        assert tenant_id in self.tenants, f"unknown tenant {tenant_id}"
        key = f"fr_{role}_{tenant_id}_{secrets.token_urlsafe(24)}"
        self._keys[self._h(key)] = Principal(principal_id, tenant_id, role)
        return key

    def resolve(self, key: str | None) -> Principal | None:
        if not key:
            return None
        return self._keys.get(self._h(key.strip()))

    def revoke(self, key: str) -> bool:
        return self._keys.pop(self._h(key), None) is not None

    def policy_for(self, tenant_id: str) -> Policy:
        t = self.tenants.get(tenant_id)
        return t.policy if t else Policy()

    def secret_for(self, tenant_id: str) -> bytes:
        t = self.tenants.get(tenant_id)
        return t.secret if t else b"unknown-tenant"


def should_sample(policy: Policy, has_error: bool, rnd: float) -> bool:
    if has_error and policy.always_sample_errors:
        return True
    return rnd < policy.sample_rate


def expiry_ns(days: int) -> int:
    return time.time_ns() + days * 86_400 * 1_000_000_000
