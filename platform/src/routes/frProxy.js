'use strict';

// Bridges the Sentinel dashboard to flight-recorder's own API so the
// dashboard can show real RAG traces/spans, not just the redaction/audit
// summaries flight-recorder forwards through /api/ingest/*. Everything here
// is a thin server-to-server proxy — the browser never talks to
// flight-recorder directly, so its X-FR-Role header stays server-side.

const express = require('express');
const { requireAuth, resolveTenantScope } = require('../middleware/authMiddleware');

const router = express.Router();

const FR_URL = process.env.FLIGHT_RECORDER_URL || 'http://localhost:8000';

// flight-recorder tenant slug <-> Sentinel tenant_id. Mirrors the mapping
// baked into flight-recorder/register_with_sentinel.py's TENANT_MAP, which
// is what registered these tenants against each other in the first place.
const FR_TENANT_BY_SENTINEL_ID = {
  't-northwind': 'northwind',
  't-acme': 'acmehealth',
  't-zephyr': 'zephyr',
};

// Sentinel role -> flight-recorder role (see flight-recorder/fr/governance.py
// ROLES), matched by equivalent privilege: platform_admin gets full access,
// compliance gets the investigatory (break-glass-capable) role, app_owner
// gets a developer's view scoped to their own tenant.
const FR_ROLE_BY_SENTINEL_ROLE = {
  platform_admin: 'admin',
  compliance: 'security',
  app_owner: 'developer',
};

function frTenantParam(req, res) {
  const scoped = resolveTenantScope(req);
  const sentinelId = scoped || req.query.tenant;
  const frTenant = sentinelId && FR_TENANT_BY_SENTINEL_ID[sentinelId];
  if (!frTenant) {
    res.status(400).json({
      error: 'pass ?tenant=<sentinel tenant id> for a flight-recorder-connected tenant',
      available: Object.keys(FR_TENANT_BY_SENTINEL_ID),
    });
    return null;
  }
  return frTenant;
}

async function frFetch(req, res, path) {
  const role = FR_ROLE_BY_SENTINEL_ROLE[req.user.role] || 'responder';
  try {
    const upstream = await fetch(`${FR_URL}${path}`, { headers: { 'X-FR-Role': role } });
    const body = await upstream.json().catch(() => ({}));
    res.status(upstream.status).json(body);
  } catch (err) {
    res.status(502).json({ error: `flight-recorder unreachable at ${FR_URL}: ${err.message}` });
  }
}

// Which flight-recorder tenants this signed-in user is allowed to look at.
router.get('/fr/tenants', requireAuth, (req, res) => {
  const scoped = req.user.role === 'app_owner' ? req.user.tenant_id : req.query.tenant;
  const entries = Object.entries(FR_TENANT_BY_SENTINEL_ID)
    .filter(([sentinelId]) => !scoped || scoped === 'all' || scoped === sentinelId)
    .map(([sentinelId, frTenant]) => ({ sentinelTenantId: sentinelId, frTenant }));
  res.json({ tenants: entries });
});

router.get('/fr/traces', requireAuth, async (req, res) => {
  const frTenant = frTenantParam(req, res);
  if (!frTenant) return;
  const limit = Math.min(Math.max(parseInt(req.query.limit, 10) || 60, 1), 200);
  await frFetch(req, res, `/api/traces?tenant=${encodeURIComponent(frTenant)}&limit=${limit}`);
});

router.get('/fr/trace/:id', requireAuth, async (req, res) => {
  const frTenant = frTenantParam(req, res);
  if (!frTenant) return;
  await frFetch(req, res, `/api/trace/${encodeURIComponent(req.params.id)}?tenant=${encodeURIComponent(frTenant)}`);
});

router.get('/fr/payloads/:id', requireAuth, async (req, res) => {
  const frTenant = frTenantParam(req, res);
  if (!frTenant) return;
  await frFetch(req, res, `/api/payloads/${encodeURIComponent(req.params.id)}?tenant=${encodeURIComponent(frTenant)}`);
});

router.get('/fr/stats', requireAuth, async (req, res) => {
  const frTenant = frTenantParam(req, res);
  if (!frTenant) return;
  await frFetch(req, res, `/api/stats?tenant=${encodeURIComponent(frTenant)}`);
});

// Generates fresh live RAG traffic through flight-recorder. Per the sink fix
// in flight-recorder/server.py's /api/inject, this fans out to both
// flight-recorder's own DB and Sentinel's ingest API in one shot — this is
// the button that actually keeps both dashboards showing the same activity.
router.post('/fr/inject', requireAuth, async (req, res) => {
  const scenario = (req.body && req.body.scenario) || 'demo';
  const count = Math.min(Math.max(parseInt(req.body && req.body.count, 10) || 5, 1), 50);
  try {
    const upstream = await fetch(
      `${FR_URL}/api/inject?scenario=${encodeURIComponent(scenario)}&count=${count}`,
      { method: 'POST' }
    );
    const body = await upstream.json().catch(() => ({}));
    res.status(upstream.status).json(body);
  } catch (err) {
    res.status(502).json({ error: `flight-recorder unreachable at ${FR_URL}: ${err.message}` });
  }
});

module.exports = router;
