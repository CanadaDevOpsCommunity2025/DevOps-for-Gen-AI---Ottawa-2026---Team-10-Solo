'use strict';

const express = require('express');
const crypto = require('node:crypto');
const db = require('../db');
const { requireIngestKey } = require('../middleware/authMiddleware');
const { recordAudit } = require('../recordAudit');
const { POLICY_VERSION, DETECTOR_VERSION } = require('../redact');

const router = express.Router();

// All routes here are called server-to-server by the SDK running inside a
// monitored application, authenticated with that app's ingest key (not a
// user JWT). The SDK has already redacted any raw text locally — nothing
// unmasked ever reaches these handlers.

router.post('/telemetry', requireIngestKey, (req, res) => {
  const app = req.app_;
  const { field, redactedText, entities } = req.body || {};
  if (!field || typeof redactedText !== 'string') {
    return res.status(400).json({ error: 'field and redactedText are required' });
  }
  const entityList = Array.isArray(entities) ? entities : [];

  db.prepare(
    `INSERT INTO telemetry_events (id, app_id, tenant_id, field, redacted_text, entities, created_at)
     VALUES (?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(crypto.randomUUID(), app.id, app.tenant_id, field, redactedText, JSON.stringify(entityList));

  const declared = new Set(JSON.parse(app.declared_pii || '[]'));
  const alreadyFlagged = new Set(
    db
      .prepare(`SELECT detail FROM audit_events WHERE app_id = ? AND event_type = 'undeclared_pii_detected'`)
      .all(app.id)
      .map((r) => (r.detail.match(/entity type ([A-Z_]+)/) || [])[1])
  );

  entityList.forEach((e) => {
    db.prepare(
      `INSERT INTO redaction_events (id, app_id, tenant_id, field, entity_type, action, policy_version, detector_version, created_at)
       VALUES (?, ?, ?, ?, ?, 'redacted', ?, ?, datetime('now'))`
    ).run(crypto.randomUUID(), app.id, app.tenant_id, field, e.type, POLICY_VERSION, DETECTOR_VERSION);

    if (!declared.has(e.type) && !alreadyFlagged.has(e.type)) {
      alreadyFlagged.add(e.type);
      recordAudit({
        appId: app.id,
        tenantId: app.tenant_id,
        eventType: 'undeclared_pii_detected',
        severity: 'high',
        detail: `Observed entity type ${e.type} in field "${field}" which is not present in this app's declared PII profile (${[...declared].join(', ') || 'none'}). The AI agent that built this app may not have anticipated this data category.`,
        actor: 'redaction-pipeline',
      });
    }
  });

  res.status(201).json({ stored: true, entitiesDetected: entityList.length });
});

router.post('/access', requireIngestKey, (req, res) => {
  const app = req.app_;
  const { userEmail, action, result, sourceIp } = req.body || {};
  if (!userEmail || !action || !result) {
    return res.status(400).json({ error: 'userEmail, action, and result are required' });
  }
  db.prepare(
    `INSERT INTO access_events (id, app_id, tenant_id, user_email, action, result, source_ip, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(crypto.randomUUID(), app.id, app.tenant_id, userEmail, action, result, sourceIp || req.ip);
  res.status(201).json({ stored: true });
});

router.post('/audit', requireIngestKey, (req, res) => {
  const app = req.app_;
  const { eventType, severity, detail, actor } = req.body || {};
  if (!eventType || !severity) return res.status(400).json({ error: 'eventType and severity are required' });
  const { event, alert } = recordAudit({ appId: app.id, tenantId: app.tenant_id, eventType, severity, detail, actor });
  res.status(201).json({ stored: true, event, alerted: !!alert });
});

router.post('/dataflow', requireIngestKey, (req, res) => {
  const app = req.app_;
  const { direction, counterparty, counterpartyAppId, dataCategories, note } = req.body || {};
  if (!direction || !counterparty) return res.status(400).json({ error: 'direction and counterparty are required' });

  let flagged = 0;
  let flowNote = note || null;

  if (counterpartyAppId) {
    const other = db.prepare('SELECT * FROM apps WHERE id = ?').get(counterpartyAppId);
    if (other && other.tenant_id !== app.tenant_id) {
      flagged = 1;
      flowNote = `Cross-tenant data flow: ${app.name} (tenant ${app.tenant_id}) exchanged data with ${other.name} (tenant ${other.tenant_id})`;
      recordAudit({
        appId: app.id,
        tenantId: app.tenant_id,
        eventType: 'cross_tenant_data_flow_detected',
        severity: 'critical',
        detail: flowNote,
        actor: 'tenancy-monitor',
      });
    }
  }

  db.prepare(
    `INSERT INTO data_flows (id, app_id, tenant_id, direction, counterparty, counterparty_app_id, data_categories, flagged, note, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(
    crypto.randomUUID(),
    app.id,
    app.tenant_id,
    direction,
    counterparty,
    counterpartyAppId || null,
    JSON.stringify(dataCategories || []),
    flagged,
    flowNote
  );

  res.status(201).json({ stored: true, flagged: !!flagged });
});

module.exports = router;
