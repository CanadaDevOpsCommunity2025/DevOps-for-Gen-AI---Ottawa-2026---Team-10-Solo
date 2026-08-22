'use strict';

const { verifyToken } = require('../auth');
const db = require('../db');
const crypto = require('node:crypto');

// Verifies the dashboard/API JWT (issued at /api/auth/login) and attaches
// req.user = { id, email, role, tenant_id }.
function requireAuth(req, res, next) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: 'missing bearer token' });
  try {
    const payload = verifyToken(token);
    req.user = { id: payload.sub, email: payload.email, role: payload.role, tenant_id: payload.tenant_id };
    next();
  } catch (err) {
    return res.status(401).json({ error: 'invalid or expired token' });
  }
}

function requireRole(...roles) {
  return (req, res, next) => {
    if (!req.user || !roles.includes(req.user.role)) {
      return res.status(403).json({ error: 'forbidden — insufficient role' });
    }
    next();
  };
}

// Resolves which tenant_id a request is scoped to.
// - app_owner: always forced to their own tenant, regardless of query params.
// - platform_admin / compliance: may pass ?tenant=<id> to view a specific
//   tenant, or omit it to see all tenants. Any cross-tenant admin read is
//   itself logged as an access_event, per the tenant-isolation requirement.
function resolveTenantScope(req) {
  if (req.user.role === 'app_owner') return req.user.tenant_id;
  const requested = req.query.tenant;
  if (requested && requested !== 'all') {
    db.prepare(
      `INSERT INTO access_events (id, app_id, tenant_id, user_email, action, result, source_ip, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
    ).run(
      crypto.randomUUID(),
      'platform',
      requested,
      req.user.email,
      `cross_tenant_admin_read:${req.originalUrl}`,
      'success',
      req.ip
    );
    return requested;
  }
  return null; // null = all tenants (admin/compliance only)
}

// Verifies the per-app ingest key used by the SDK (server-to-server, no
// JWT). Attaches req.app = the resolved app row.
function requireIngestKey(req, res, next) {
  const appId = req.headers['x-app-id'];
  const ingestKey = req.headers['x-ingest-key'];
  if (!appId || !ingestKey) {
    return res.status(401).json({ error: 'missing x-app-id / x-ingest-key headers' });
  }
  const app = db.prepare('SELECT * FROM apps WHERE id = ? AND ingest_key = ?').get(appId, ingestKey);
  if (!app) return res.status(401).json({ error: 'unknown app or invalid ingest key' });
  req.app_ = app; // "app" is reserved-ish on req in some setups; use app_
  next();
}

module.exports = { requireAuth, requireRole, resolveTenantScope, requireIngestKey };
