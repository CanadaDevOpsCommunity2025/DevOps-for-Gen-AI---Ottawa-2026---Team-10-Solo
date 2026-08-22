"""
Redaction engine.

Design rule: raw sensitive text must never leave the application process. The
SDK redacts before the span is serialised; the collector re-scans on arrival and
raises `escaped` if it finds anything the SDK missed. That second pass is what
turns "we promise we redact" into something you can put on a dashboard.

Replacement is a deterministic token, not a black bar:

    alice@corp.com  ->  <EMAIL:7f2a>

The suffix is HMAC(tenant_secret, normalised_value) truncated. Same person gets
the same token across every span and every trace, so you can still answer "did
this user hit the same failure twice" without ever holding the address. Change
the tenant secret and the linkage is gone.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Callable, Iterable

# --- detectors --------------------------------------------------------------
# Each returns (label, match_object) spans. Ordering matters: longer, more
# specific patterns run first so a credit card isn't half-eaten by a phone rule.

_LUHN_CANDIDATE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    checksum, parity = 0, len(ds) % 2
    for i, d in enumerate(ds):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


PATTERNS: list[tuple[str, re.Pattern]] = [
    ("JWT",          re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("AWS_KEY",      re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("OPENAI_KEY",   re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("ANTHROPIC_KEY", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("PRIVATE_KEY",  re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("EMAIL",        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IBAN",         re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
    ("SSN",          re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("SIN_CA",       re.compile(r"\b\d{3}[ -]\d{3}[ -]\d{3}\b")),
    ("PHONE",        re.compile(r"(?<![\d.])(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d.])")),
    ("IPV4",         re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("DOB",          re.compile(r"\b(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])\b")),
    ("MRN",          re.compile(r"\bMRN[:\s#]*[A-Z0-9-]{5,}\b", re.I)),
    ("PASSPORT",     re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")),
]

# Names need a model, not a regex. The engine takes an optional pluggable NER
# callable so you can drop in Presidio / spaCy / a local model without touching
# this file. Signature: (text) -> Iterable[(label, start, end)]
NerFn = Callable[[str], Iterable[tuple[str, int, int]]]


@dataclass
class Finding:
    label: str
    start: int
    end: int
    token: str


class Redactor:
    """
    modes:
      off     - pass through, count findings only (use for a shadow rollout so
                you can measure PII pressure before you turn enforcement on)
      mask    - replace with <LABEL:hash>          [default]
      hash    - replace the entire field with a digest, keep length metadata
      drop    - discard the payload entirely, keep the finding counts
    """

    def __init__(self, secret: bytes, mode: str = "mask",
                 allow_labels: set[str] | None = None, ner: NerFn | None = None):
        self.secret = secret
        self.mode = mode
        # labels the tenant has explicitly decided are safe to keep in the clear
        self.allow = allow_labels or set()
        self.ner = ner

    def _token(self, label: str, value: str) -> str:
        norm = re.sub(r"[\s()\-.]", "", value).lower()
        h = hmac.new(self.secret, f"{label}:{norm}".encode(), hashlib.sha256).hexdigest()[:6]
        return f"<{label}:{h}>"

    def scan(self, text: str) -> list[Finding]:
        if not text:
            return []
        raw: list[tuple[str, int, int]] = []

        for label, pat in PATTERNS:
            if label in self.allow:
                continue
            for m in pat.finditer(text):
                raw.append((label, m.start(), m.end()))

        for m in _LUHN_CANDIDATE.finditer(text):
            if _luhn_ok(m.group()) and "CREDIT_CARD" not in self.allow:
                raw.append(("CREDIT_CARD", m.start(), m.end()))

        if self.ner:
            for label, s, e in self.ner(text):
                if label not in self.allow:
                    raw.append((label, s, e))

        # resolve overlaps: longest match wins, then earliest
        raw.sort(key=lambda t: (t[1], -(t[2] - t[1])))
        chosen: list[tuple[str, int, int]] = []
        cursor = -1
        for label, s, e in raw:
            if s >= cursor:
                chosen.append((label, s, e))
                cursor = e

        return [Finding(l, s, e, self._token(l, text[s:e])) for l, s, e in chosen]

    def redact(self, text: str) -> tuple[str, dict]:
        """Returns (safe_text, report). report never contains the raw values."""
        findings = self.scan(text)
        counts: dict[str, int] = {}
        for f in findings:
            counts[f.label] = counts.get(f.label, 0) + 1

        report = {
            "findings": counts,
            "count": len(findings),
            "mode": self.mode,
            "chars_in": len(text or ""),
        }

        if self.mode == "drop":
            return "", {**report, "dropped": True}
        if self.mode == "hash":
            digest = hashlib.sha256((text or "").encode()).hexdigest()[:16]
            return f"<CONTENT:{digest}:{len(text or '')}chars>", report
        if self.mode == "off" or not findings:
            return text, report

        out, prev = [], 0
        for f in findings:
            out.append(text[prev:f.start])
            out.append(f.token)
            prev = f.end
        out.append(text[prev:])
        return "".join(out), report


def fingerprint(text: str) -> str:
    """
    For system prompts. You almost never need to read a system prompt to debug,
    but you very often need to know *which version* was live. Store the
    fingerprint, not the prompt.
    """
    return "sp_" + hashlib.sha256((text or "").encode()).hexdigest()[:12]
