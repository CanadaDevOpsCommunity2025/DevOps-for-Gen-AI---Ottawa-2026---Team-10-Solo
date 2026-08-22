'use strict';

const crypto = require('node:crypto');
const db = require('./db');
const { routeAlertIfNeeded } = require('./alerts');

// Central helper for writing an audit event and triggering alert routing.
// audit_events is treated as append-only (write-once): nothing in this
// codebase issues UPDATE/DELETE against it outside the governed retention
// sweep in routes/logs.js.
function recordAudit({ appId, tenantId, eventType, severity, detail, actor }) {
  const id = crypto.randomUUID();
  db.prepare(
    `INSERT INTO audit_events (id, app_id, tenant_id, event_type, severity, detail, actor, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(id, appId, tenantId, eventType, severity, detail || null, actor || null);

  const event = { id, app_id: appId, tenant_id: tenantId, event_type: eventType, severity, detail, actor };
  const alert = routeAlertIfNeeded(event);
  return { event, alert };
}

module.exports = { recordAudit };
