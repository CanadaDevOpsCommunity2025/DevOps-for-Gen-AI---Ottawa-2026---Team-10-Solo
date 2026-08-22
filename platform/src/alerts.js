'use strict';

const crypto = require('node:crypto');
const db = require('./db');

// Alert routing: whenever an audit event of severity high/critical is
// recorded, notify (a) the platform administrator and (b) the registered
// owner of the emitting application. A real deployment would plug in
// email/Slack/webhook here; for the demo we persist a row in `alerts`
// (surfaced in the dashboard's notification feed) and print to stdout,
// which is the same integration point a real notifier would hook into.
function routeAlertIfNeeded(auditEvent) {
  if (!['high', 'critical'].includes(auditEvent.severity)) return null;

  const app = db.prepare('SELECT * FROM apps WHERE id = ?').get(auditEvent.app_id);
  const recipients = ['platform-admin@sentinel.internal'];
  if (app && app.owner_email) recipients.push(app.owner_email);

  const message = `[${auditEvent.severity.toUpperCase()}] ${auditEvent.event_type} on "${app ? app.name : auditEvent.app_id}": ${auditEvent.detail || ''}`;

  const alertId = crypto.randomUUID();
  db.prepare(
    `INSERT INTO alerts (id, audit_event_id, app_id, tenant_id, severity, message, recipients, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(alertId, auditEvent.id, auditEvent.app_id, auditEvent.tenant_id, auditEvent.severity, message, JSON.stringify(recipients));

  // eslint-disable-next-line no-console
  console.log(`[ALERT] -> ${recipients.join(', ')} :: ${message}`);

  return { id: alertId, recipients, message };
}

module.exports = { routeAlertIfNeeded };
