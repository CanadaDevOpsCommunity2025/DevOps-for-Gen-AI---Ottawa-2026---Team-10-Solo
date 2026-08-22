'use strict';

const express = require('express');
const db = require('../db');
const { requireAuth, requireRole, resolveTenantScope } = require('../middleware/authMiddleware');
const { evaluateRetention, regulationsForRegion, PII_REGULATION_MAP } = require('../compliance');
const { recordAudit } = require('../recordAudit');

const router = express.Router();

function scoped(table, tenant, extraWhere = '', extraArgs = []) {
  if (tenant) {
    return db.prepare(`SELECT * FROM ${table} WHERE tenant_id = ? ${extraWhere} ORDER BY created_at DESC LIMIT 200`).all(tenant, ...extraArgs);
  }
  return db.prepare(`SELECT * FROM ${table} ${extraWhere ? extraWhere.replace(/^AND/i, 'WHERE') : ''} ORDER BY created_at DESC LIMIT 200`).all(...extraArgs);
}

router.get('/tenants', requireAuth, requireRole('platform_admin', 'compliance'), (req, res) => {
  res.json(db.prepare('SELECT * FROM tenants ORDER BY name').all());
});

router.get('/overview', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  const apps = tenant ? db.prepare('SELECT * FROM apps WHERE tenant_id = ?').all(tenant) : db.prepare('SELECT * FROM apps').all();
  const audits = scoped('audit_events', tenant);
  const flows = scoped('data_flows', tenant);

  const criticalCount = audits.filter((a) => a.severity === 'critical').length;
  const highCount = audits.filter((a) => a.severity === 'high').length;
  const flaggedFlows = flows.filter((f) => f.flagged).length;

  const appsSummary = apps.map((a) => {
    const undeclaredDrift = audits.some((e) => e.app_id === a.id && e.event_type === 'undeclared_pii_detected');
    return {
      id: a.id,
      name: a.name,
      tenant_id: a.tenant_id,
      region: a.region,
      retention_days: a.retention_days,
      owner_email: a.owner_email,
      declared_pii: JSON.parse(a.declared_pii || '[]'),
      applicable_regulations: regulationsForRegion(a.region),
      drift: undeclaredDrift,
    };
  });

  res.json({
    criticalCount,
    highCount,
    appCount: apps.length,
    flaggedFlowCount: flaggedFlows,
    apps: appsSummary,
    latestAudits: audits.slice(0, 8),
  });
});

router.get('/redaction-events', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('redaction_events', tenant));
});

router.get('/telemetry-events', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('telemetry_events', tenant));
});

router.get('/audit-events', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('audit_events', tenant));
});

router.get('/access-events', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('access_events', tenant));
});

router.get('/alerts', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('alerts', tenant));
});

router.get('/dataflows', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  res.json(scoped('data_flows', tenant));
});

router.get('/retention', requireAuth, (req, res) => {
  const tenant = resolveTenantScope(req);
  const apps = tenant ? db.prepare('SELECT * FROM apps WHERE tenant_id = ?').all(tenant) : db.prepare('SELECT * FROM apps').all();
  const rows = apps.map((a) => {
    const { status, reasons } = evaluateRetention(a);
    return {
      id: a.id,
      name: a.name,
      region: a.region,
      retention_days: a.retention_days,
      deletion_allowed: !!a.deletion_allowed,
      applicable_regulations: regulationsForRegion(a.region),
      status,
      reasons,
    };
  });
  res.json({ apps: rows, piiRegulationMap: PII_REGULATION_MAP });
});

// Simulates the "scheduled nightly job" that cross-checks retention/region
// compliance. Exposed as an on-demand endpoint for the demo; a real
// deployment would run this on a cron/interval.
router.post('/compliance/sweep', requireAuth, requireRole('platform_admin', 'compliance'), (req, res) => {
  const apps = db.prepare('SELECT * FROM apps').all();
  const flagged = [];
  apps.forEach((a) => {
    const { status, reasons } = evaluateRetention(a);
    if (status === 'at_risk') {
      const recent = db
        .prepare(
          `SELECT COUNT(*) as c FROM audit_events WHERE app_id = ? AND event_type = 'retention_check_failed' AND created_at > datetime('now', '-1 day')`
        )
        .get(a.id);
      if (recent.c === 0) {
        recordAudit({
          appId: a.id,
          tenantId: a.tenant_id,
          eventType: 'retention_check_failed',
          severity: 'high',
          detail: reasons.join('; '),
          actor: 'compliance-sweep',
        });
        flagged.push(a.name);
      }
    } else {
      recordAudit({
        appId: a.id,
        tenantId: a.tenant_id,
        eventType: 'retention_check_passed',
        severity: 'info',
        detail: 'Nightly retention sweep: within policy',
        actor: 'compliance-sweep',
      });
    }
  });
  res.json({ sweepComplete: true, appsChecked: apps.length, newlyFlagged: flagged });
});

module.exports = router;
