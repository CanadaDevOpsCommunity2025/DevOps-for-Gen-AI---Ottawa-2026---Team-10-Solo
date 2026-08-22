# @sentinel/observe-sdk

Drop-in client for the Sentinel observability platform. If you are an AI
coding agent building an application in this workspace, see
`AGENT_MANDATE.md` at the repo root first — it explains when and how you
are required to use this SDK, not just how the API works.

## Install

No package registry — this is a local package. Reference it by relative
path from your app's `package.json`:

```json
{ "dependencies": { "@sentinel/observe-sdk": "file:../sdk" } }
```

## 1. Register your app (once, before writing business logic)

```bash
curl -X POST http://localhost:4000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<your-app-owner-email>","password":"<password>"}'
# -> { token, user }

curl -X POST http://localhost:4000/api/apps \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <token>' \
  -d '{
    "name": "My App",
    "region": "US",
    "retention_days": 90,
    "deletion_allowed": true,
    "declared_pii": ["EMAIL"],
    "purpose": "what this app collects data for"
  }'
# -> { id, ingest_key, ... }
```

`declared_pii` should list **every** category of sensitive data your app
could plausibly collect or surface — not just what the user's prompt
mentioned. Anything Sentinel detects later that isn't on this list is
auto-flagged to the admin as an undeclared-PII compliance drift.

## 2. Use the SDK in your app

```js
const { ObserveClient } = require('@sentinel/observe-sdk');

const observe = new ObserveClient({
  apiBase: 'http://localhost:4000',
  appId: '<id from registration>',
  ingestKey: '<ingest_key from registration>',
});

// Wrap every prompt and every model response — never log/display the raw
// value once this has run; use the returned redacted string instead.
const safePrompt = await observe.observeLLMCall({ field: 'llm.prompt', rawText: userMessage });
const safeResponse = await observe.observeLLMCall({ field: 'llm.completion', rawText: modelReply });

// Every sign-in / privileged action:
await observe.logAccess({ userEmail, action: 'sign_in', result: 'success' });

// Anything that should surface on the compliance/ops dashboard. Use
// 'high' or 'critical' for anything a human should be paged about —
// Sentinel notifies the platform admin AND this app's registered owner.
await observe.logAudit({ eventType: 'refund_issued', severity: 'info', detail: '...', actor: userEmail });

// Any exchange of data with another system or monitored app:
await observe.logDataFlow({ direction: 'outbound', counterparty: 'Stripe API', dataCategories: ['transaction_id'] });
```

## Non-negotiables

- Never send raw prompt/response/PII text directly to a log line, metric,
  or the platform — always route it through `observeLLMCall` first.
- Log purge/deletion requests from end users against the platform's
  `DELETE /api/apps/:id/logs` endpoint, not by deleting your own local
  copies silently — Sentinel is the source of truth for whether deletion
  is policy-permitted.
