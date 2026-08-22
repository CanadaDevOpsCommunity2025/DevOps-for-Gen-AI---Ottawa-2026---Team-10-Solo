'use strict';

const express = require('express');
const crypto = require('node:crypto');
const db = require('../db');
const { requireAuth, requireRole, resolveTenantScope } = require('../middleware/authMiddleware');
const { regulationsForRegion, evaluateRetention } = require('../compliance');
const { recordAudit } = require('../recordAudit');

const router = express.Router();

function enrich(app) {
  const declared = JSON.parse(app.declared_pii || '[]');
  const retention = evaluateRetention(app);
  return {
    id: app.id,
    name: app.name,
    tenant_id: app.tenant_id,
    owner_email: app.owner_email,
    region: app.region,
    retention_days: app.retention_days,
    deletion_allowed: !!app.deletion_allowed,
    declared_pii: declared,
    purpose: app.purpose,
    applicable_regulations: regulationsForRegion(app.region),
    retention_status: retention.status,
    retention_reasons: retention.reasons,
    created_at: app.created_at,
  };
}

// ---- Register a new monitored application ----
// This is the "mandate" endpoint: an AI agent building an app is required
// to call this (directly, or via the register.js bootstrap script) before
// the app is allowed to emit any telemetry. The declared_pii list becomes
// the baseline the drift detector compares real traffic against.
router.post('/', requireAuth, requireRole('app_owner', 'platform_admin'), (req, res) => {
  const { name, region, declared_pii, purpose, retention_days, deletion_allowed, tenant_id } = req.body || {};

  if (!name || !region || !retention_days) {
    return res.status(400).json({ error: 'name, region, and retention_days are required' });
  }

  const targetTenant = req.user.role === 'app_owner' ? req.user.tenant_id : tenant_id;
  if (!targetTenant) return res.status(400).json({ error: 'tenant_id is required for admin-initiated registration' });

  const id = `app-${crypto.randomBytes(4).toString('hex')}`;
  const ingestKey = crypto.randomBytes(24).toString('hex');

  db.prepare(
    `INSERT INTO apps (id, name, tenant_id, owner_email, region, retention_days, deletion_allowed, declared_pii, purpose, ingest_key, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(
    id,
    name,
    targetTenant,
    req.user.email,
    region,
    retention_days,
    deletion_allowed ? 1 : 0,
    JSON.stringify(declared_pii || []),
    purpose || null,
    ingestKey
  );

  recordAudit({
    appId: id,
    tenantId: targetTenant,
    eventType: 'app_registered',
    severity: 'info',
    detail: `Registered with declared PII profile: ${(declared_pii || []).join(', ') || '(none declared)'}`,
    actor: req.user.email,
  });

  const app = db.prepare('SELECT * FROM apps WHERE id = ?').get(id);
  res.status(201).json({ ...enrich(app), ingest_key: ingestKey });
});

router.get('/', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  const rows = tenant ? db.prepare('SELECT * FROM apps WHERE tenant_id = ?').all(tenant) : db.prepare('SELECT * FROM apps').all();
  res.json(rows.map(enrich));
});

router.get('/:id', requireAuth, (req, res) => {
  const app = db.prepare('SELECT * FROM apps WHERE id = ?').get(req.params.id);
  if (!app) return res.status(404).json({ error: 'not found' });
  if (req.user.role === 'app_owner' && app.tenant_id !== req.user.tenant_id) {
    return res.status(403).json({ error: 'forbidden — not your tenant' });
  }
  res.json(enrich(app));
});

// ---- Log purge / deletion request ----
// Demonstrates that retention/deletion policy is enforced by the platform
// independent of what the source app (or its builder) wants: purge is only
// permitted when the app's own policy allows deletion at all, and only for
// records that have already passed the declared retention floor.
router.delete('/:id/logs', requireAuth, (req, res) => {
  const app = db.prepare('SELECT * FROM apps WHERE id = ?').get(req.params.id);
  if (!app) return res.status(404).json({ error: 'not found' });
  if (req.user.role === 'app_owner' && app.tenant_id !== req.user.tenant_id) {
    return res.status(403).json({ error: 'forbidden — not your tenant' });
  }

  const reason = (req.body && req.body.reason) || 'unspecified';

  if (!app.deletion_allowed) {
    recordAudit({
      appId: app.id,
      tenantId: app.tenant_id,
      eventType: 'log_purge_attempt',
      severity: 'high',
      detail: `Purge blocked — this application's policy does not permit log deletion. Reason given: ${reason}`,
      actor: req.user.email,
    });
    return res.status(403).json({ allowed: false, reason: 'deletion_allowed=false for this application' });
  }

  const cutoff = `-${app.retention_days} days`;
  const del = (table) =>
    db.prepare(`DELETE FROM ${table} WHERE app_id = ? AND created_at < datetime('now', ?)`).run(app.id, cutoff).changes;

  const deletedTelemetry = del('telemetry_events');
  const deletedAccess = del('access_events');
  const totalDeleted = deletedTelemetry + deletedAccess;

  recordAudit({
    appId: app.id,
    tenantId: app.tenant_id,
    eventType: 'log_purge_attempt',
    severity: totalDeleted > 0 ? 'warn' : 'info',
    detail: `Purge allowed — removed ${totalDeleted} record(s) older than the ${app.retention_days}d retention floor. Reason given: ${reason}`,
    actor: req.user.email,
  });

  res.json({ allowed: true, deleted: totalDeleted });
});

module.exports = router;
