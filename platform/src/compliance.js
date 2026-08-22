'use strict';

// Static regulation-mapping reference used by the drift-detection and
// retention-compliance checks. In a production system this table would be
// versioned and reviewed by legal/compliance, not hardcoded — kept static
// here since the mapping logic, not the legal content, is what's real.

const REGION_REGULATIONS = {
  US: ['CCPA (if CA residents)'],
  EU: ['GDPR'],
  CA: ['PIPEDA'],
  UK: ['UK GDPR'],
};

const PII_REGULATION_MAP = {
  SIN: 'PIPEDA (Canada) — government ID, high sensitivity',
  CREDIT_CARD: 'PCI-DSS — payment card data',
  EMAIL: 'GDPR / CCPA — direct identifier',
  ADDRESS: 'GDPR / CCPA — direct identifier',
  NAME: 'GDPR / CCPA / PIPEDA — direct identifier',
  HEALTH_RECORD: 'HIPAA (US) / GDPR Art.9 special category (EU)',
  PHONE: 'GDPR / CCPA — direct identifier',
  DOB: 'GDPR / CCPA — direct identifier',
  ORDER_ID: 'Low sensitivity — internal reference',
};

const GDPR_MAX_RETENTION_DAYS = 30; // storage-limitation heuristic used for the demo's "at risk" flag

function regulationsForRegion(region) {
  return REGION_REGULATIONS[region] || [];
}

function regulationForPii(entityType) {
  return PII_REGULATION_MAP[entityType] || 'No specific mapping on file — review manually';
}

/**
 * Evaluates one app's retention/region posture.
 * Returns { status: 'compliant'|'at_risk', reasons: string[] }
 */
function evaluateRetention(app) {
  const reasons = [];
  if (app.region === 'EU' && app.retention_days > GDPR_MAX_RETENTION_DAYS) {
    reasons.push(
      `Serves EU users but retains data for ${app.retention_days}d, exceeding the ${GDPR_MAX_RETENTION_DAYS}d GDPR storage-limitation heuristic`
    );
  }
  if (!app.deletion_allowed && app.retention_days > 365) {
    reasons.push('Deletion disabled and retention exceeds 1 year — review lawful basis for indefinite retention');
  }
  return { status: reasons.length ? 'at_risk' : 'compliant', reasons };
}

module.exports = {
  REGION_REGULATIONS,
  PII_REGULATION_MAP,
  regulationsForRegion,
  regulationForPii,
  evaluateRetention,
};
