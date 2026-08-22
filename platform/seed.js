'use strict';

// Seeds baseline tenants, users, and a couple of already-onboarded example
// apps so the dashboard has real historical data to look at from a fresh
// checkout. Does NOT create the "RAG Support Agent" apps for northwind/acme/
// zephyr — those are registered live by flight-recorder/register_with_sentinel.py,
// so you can watch that traffic generator's apps get onboarded end to end.
//
// Run: node seed.js          (skips if already seeded)
//      node seed.js --force  (wipes and reseeds)

const crypto = require('node:crypto');
const db = require('./src/db');
const { hashPassword } = require('./src/auth');
const { recordAudit } = require('./src/recordAudit');

const FORCE = process.argv.includes('--force');

const existing = db.prepare('SELECT COUNT(*) as c FROM tenants').get();
if (existing.c > 0 && !FORCE) {
  console.log('Already seeded (tenants exist). Run with --force to wipe and reseed.');
  process.exit(0);
}

if (FORCE) {
  [
    'alerts',
    'data_flows',
    'audit_events',
    'access_events',
    'redaction_events',
    'telemetry_events',
    'apps',
    'users',
    'tenants',
  ].forEach((t) => db.exec(`DELETE FROM ${t};`));
}

function insertUser(email, password, role, tenantId) {
  const { hash, salt } = hashPassword(password);
  db.prepare(
    `INSERT INTO users (id, email, password_hash, salt, role, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(crypto.randomUUID(), email, hash, salt, role, tenantId);
}

function insertApp({ id, name, tenant, owner, region, retentionDays, deletionAllowed, declaredPii, purpose }) {
  const ingestKey = crypto.randomBytes(24).toString('hex');
  db.prepare(
    `INSERT INTO apps (id, name, tenant_id, owner_email, region, retention_days, deletion_allowed, declared_pii, purpose, ingest_key, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(id, name, tenant, owner, region, retentionDays, deletionAllowed ? 1 : 0, JSON.stringify(declaredPii), purpose, ingestKey);
  return ingestKey;
}

// ---- Tenants ----
db.prepare(`INSERT INTO tenants (id, name, region, created_at) VALUES (?, ?, ?, datetime('now'))`).run('t-northwind', 'Northwind Retail', 'US');
db.prepare(`INSERT INTO tenants (id, name, region, created_at) VALUES (?, ?, ?, datetime('now'))`).run('t-acme', 'Acme Health', 'EU');
db.prepare(`INSERT INTO tenants (id, name, region, created_at) VALUES (?, ?, ?, datetime('now'))`).run('t-lumen', 'Lumen Finance', 'CA');
// t-zephyr exists so flight-recorder/register_with_sentinel.py has a Sentinel
// tenant to register its "zephyr" RAG traffic against — see the "Bridge to
// Sentinel" section of flight-recorder/README.md
db.prepare(`INSERT INTO tenants (id, name, region, created_at) VALUES (?, ?, ?, datetime('now'))`).run('t-zephyr', 'Zephyr Financial', 'US');

// ---- Users ----
// Admin/compliance passwords are overridable via env — set these before
// seeding a deployment reachable outside your own machine. App-owner demo
// accounts stay on fixed demo passwords; they're tenant-scoped only.
const ADMIN_PASSWORD = process.env.SENTINEL_ADMIN_PASSWORD || 'admin123';
const COMPLIANCE_PASSWORD = process.env.SENTINEL_COMPLIANCE_PASSWORD || 'compliance123';
if (!process.env.SENTINEL_ADMIN_PASSWORD || !process.env.SENTINEL_COMPLIANCE_PASSWORD) {
  console.warn(
    '[seed] Using default admin/compliance passwords. Set SENTINEL_ADMIN_PASSWORD and ' +
    'SENTINEL_COMPLIANCE_PASSWORD before seeding a publicly reachable deployment.'
  );
}

insertUser('admin@sentinel.platform', ADMIN_PASSWORD, 'platform_admin', null);
insertUser('compliance@sentinel.platform', COMPLIANCE_PASSWORD, 'compliance', null);
insertUser('priya.rao@northwind.com', 'password123', 'app_owner', 't-northwind');
insertUser('jae.kim@northwind.com', 'password123', 'app_owner', 't-northwind'); // owns the RAG Support Agent app once flight-recorder registers it
insertUser('dr.chen@acmehealth.eu', 'password123', 'app_owner', 't-acme');
insertUser('m.tremblay@lumenfin.ca', 'password123', 'app_owner', 't-lumen');
insertUser('sam.okafor@zephyrfinancial.com', 'password123', 'app_owner', 't-zephyr');

// ---- Example already-onboarded apps ----
insertApp({
  id: 'app-order',
  name: 'Order Assistant',
  tenant: 't-northwind',
  owner: 'priya.rao@northwind.com',
  region: 'US',
  retentionDays: 90,
  deletionAllowed: true,
  declaredPii: ['EMAIL', 'ADDRESS'],
  purpose: 'Order status lookups and shipping updates',
});
insertApp({
  id: 'app-clinical',
  name: 'Clinical Note Summarizer',
  tenant: 't-acme',
  owner: 'dr.chen@acmehealth.eu',
  region: 'EU',
  retentionDays: 45, // intentionally exceeds the GDPR heuristic to demonstrate the retention "at risk" flag
  deletionAllowed: true,
  declaredPii: ['NAME', 'HEALTH_RECORD'],
  purpose: 'Summarizes clinician notes for referral letters',
});
insertApp({
  id: 'app-fraud',
  name: 'Fraud Triage Agent',
  tenant: 't-lumen',
  owner: 'm.tremblay@lumenfin.ca',
  region: 'CA',
  retentionDays: 180,
  deletionAllowed: false, // regulated audit trail — deletion never permitted
  declaredPii: ['SIN', 'CREDIT_CARD'],
  purpose: 'Flags suspicious transactions for manual review',
});

// ---- Historical access events ----
const accessEvents = [
  ['app-order', 't-northwind', 'priya.rao@northwind.com', 'sign_in', 'success'],
  ['app-clinical', 't-acme', 'dr.chen@acmehealth.eu', 'sign_in', 'success'],
  ['app-clinical', 't-acme', 'dr.chen@acmehealth.eu', 'data_export: patient_summaries.csv', 'success'],
  ['app-clinical', 't-acme', 'unknown@203.0.113.9', 'sign_in', 'failed (bad credentials)'],
  ['app-fraud', 't-lumen', 'm.tremblay@lumenfin.ca', 'sign_in', 'success'],
];
accessEvents.forEach(([appId, tenantId, user, action, result]) => {
  db.prepare(
    `INSERT INTO access_events (id, app_id, tenant_id, user_email, action, result, source_ip, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(crypto.randomUUID(), appId, tenantId, user, action, result, '203.0.113.9');
});

// ---- Historical data flows ----
const flows = [
  ['app-order', 't-northwind', 'outbound', 'CRM (Salesforce)', null, ['email', 'order_id'], 0, null],
  ['app-clinical', 't-acme', 'inbound', 'EHR (Epic)', null, ['patient_name', 'health_record'], 0, null],
  ['app-fraud', 't-lumen', 'outbound', 'Core Banking API', null, ['transaction_id'], 0, null],
];
flows.forEach(([appId, tenantId, direction, counterparty, counterpartyAppId, data, flagged, note]) => {
  db.prepare(
    `INSERT INTO data_flows (id, app_id, tenant_id, direction, counterparty, counterparty_app_id, data_categories, flagged, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(crypto.randomUUID(), appId, tenantId, direction, counterparty, counterpartyAppId, JSON.stringify(data), flagged, note);
});

// ---- Seed one already-blocked purge attempt on the fraud app ----
recordAudit({
  appId: 'app-fraud',
  tenantId: 't-lumen',
  eventType: 'log_purge_attempt',
  severity: 'high',
  detail: "Purge blocked — this application's policy does not permit log deletion. Reason given: user requested account data removal",
  actor: 'm.tremblay@lumenfin.ca',
});

console.log('Seed complete.');
console.log('Login as: admin@sentinel.platform / admin123 (platform admin)');
console.log('       or: compliance@sentinel.platform / compliance123 (compliance officer)');
console.log('       or: jae.kim@northwind.com / password123 (app owner — will own the RAG Support Agent app once flight-recorder registers)');
