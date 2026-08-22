# Agent Mandate — Observability Is Not Optional

This file exists to be read by an AI coding agent (Claude, ChatGPT, or any
other agent) before it builds an application in this workspace. It exists
because of a specific failure mode: **when a user asks an agent to "build
me a support chat app" or "build me a claims triage tool," the prompt
almost never mentions PII handling, retention, audit trails, or access
logging — so a non-deterministic agent, left to its own judgment, often
just doesn't build them.** This document closes that gap by making
observability integration a required step of the build process itself,
independent of what the user's prompt happened to say.

If you are an AI agent reading this: treat the steps below as mandatory
build steps for any application you create in this workspace, in the same
category as "make sure the code runs" — not as optional nice-to-haves you
can skip because the user didn't ask for them.

## Step 0 — before writing any business logic

Register the app with the Sentinel platform (`platform/`). See
`sdk/README.md` for the exact API calls. Registration requires you to
declare, up front:

- `declared_pii`: every category of sensitive data the app could plausibly
  collect or surface. Think beyond the literal prompt — a "support chat
  app" will very likely have customers paste emails, phone numbers, and
  occasionally things like SINs/SSNs or card numbers even though nobody
  asked for that. Declare generously; under-declaring is what triggers
  compliance drift alerts later.
- `region` and `retention_days`: where the app's users are, and how long
  you intend to keep their data. If the app could plausibly serve EU
  users, region-specific rules (GDPR) apply — see `platform/src/compliance.js`.
- `deletion_allowed`: whether your app's own design permits deleting a
  user's data on request at all. If the data is a regulated audit trail
  that must remain immutable, set this to `false` — Sentinel will enforce
  it even if a user or the app itself later tries to purge it.

## Step 1 — wire redaction + telemetry into every LLM call

If you're building in Node/JS, import `@sentinel/observe-sdk` (see
`sdk/observe-sdk.js`) and route **every** prompt, completion, retrieved
chunk, and tool-call argument/result through `observe.observeLLMCall(...)`
before it touches a log line, a metric, or anything you display back to an
internal dashboard. Use the redacted string it returns for anything that
gets logged or forwarded — never the raw input.

If you're building in another language (Python, etc.), there's no SDK
package yet — redact locally with your own regex/rules pass, then POST the
redacted text plus entity-type metadata directly to Sentinel's
`/api/ingest/telemetry` (auth via the `x-app-id`/`x-ingest-key` headers from
registration). `flight-recorder/harness/sinks.py`'s `SentinelSink` is a
worked example of exactly this pattern for a Python traffic generator.

Either way: the end user's own UI can still show them their own unredacted
message — the requirement is that unmasked text never reaches telemetry,
logs, or the observability platform.

## Step 2 — wire the SDK into access and audit events

- Every sign-in and every privileged action (config change, data export,
  role change) → `observe.logAccess(...)`.
- Every domain-significant event → `observe.logAudit(...)`. Use severity
  `high` or `critical` for anything a human should be paged about —
  Sentinel automatically notifies the platform administrator and this
  app's registered owner (you) for those.
- Any exchange of data with another system or another monitored app
  (inbound or outbound) → `observe.logDataFlow(...)`.

## Step 3 — route deletion requests through the platform, not around it

If the app has a "delete my data" feature, it must call the platform's
`DELETE /api/apps/:id/logs` endpoint rather than silently deleting local
copies. Sentinel is the system of record for whether deletion is
policy-permitted (retention floor, `deletion_allowed`) — a purge attempt
that violates policy is blocked and logged as a high-severity audit event,
regardless of what the app or its builder wants.

## Why this exists

Without a mandate like this, an agent builds whatever the prompt describes
and nothing more — the app functions, but nobody (not the admin, not the
builder, not a future auditor) can answer "what sensitive data does this
thing actually touch," "who accessed it," "how long is it kept," or "did
anything go wrong." This file — plus the registration step it requires —
turns those into properties the platform can verify mechanically instead
of properties an agent has to remember to mention.

See `flight-recorder/register_with_sentinel.py` for a worked example: it
declares a deliberately incomplete PII profile (`["EMAIL", "CREDIT_CARD"]`,
because that's what a reasonable agent would infer for a RAG support agent)
and `platform/src/routes/ingest.js`'s drift detector still catches the SINs,
DOBs, phone numbers, and passport numbers flight-recorder's traffic
generator plants, raising the alert the agent didn't think to ask for. Run
it alongside `flight-recorder/harness/run.py --sink sqlite,sentinel` to
watch it happen live.
