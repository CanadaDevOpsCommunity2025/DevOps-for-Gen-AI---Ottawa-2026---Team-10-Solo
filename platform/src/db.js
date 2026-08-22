'use strict';

const { DatabaseSync } = require('node:sqlite');
const path = require('node:path');
const fs = require('node:fs');

const DATA_DIR = path.join(__dirname, '..', 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

const db = new DatabaseSync(path.join(DATA_DIR, 'sentinel.db'));
db.exec('PRAGMA foreign_keys = ON;');

db.exec(`
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  region TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL, -- platform_admin | compliance | app_owner
  tenant_id TEXT,      -- NULL for platform_admin/compliance (cross-tenant)
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS apps (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  owner_email TEXT NOT NULL,
  region TEXT NOT NULL,
  retention_days INTEGER NOT NULL,
  deletion_allowed INTEGER NOT NULL DEFAULT 0,
  declared_pii TEXT NOT NULL DEFAULT '[]',   -- json array
  purpose TEXT,
  ingest_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS telemetry_events (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  field TEXT NOT NULL,
  redacted_text TEXT NOT NULL,
  entities TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS redaction_events (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  field TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  action TEXT NOT NULL,
  policy_version TEXT,
  detector_version TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS access_events (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  user_email TEXT NOT NULL,
  action TEXT NOT NULL,
  result TEXT NOT NULL,
  source_ip TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  severity TEXT NOT NULL, -- info | warn | high | critical
  detail TEXT,
  actor TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY,
  audit_event_id TEXT NOT NULL,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  recipients TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_flows (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  direction TEXT NOT NULL, -- inbound | outbound | internal
  counterparty TEXT NOT NULL,
  counterparty_app_id TEXT,
  data_categories TEXT NOT NULL DEFAULT '[]',
  flagged INTEGER NOT NULL DEFAULT 0,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
`);

module.exports = db;
