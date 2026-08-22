'use strict';

// Canonical PII/sensitive-data detection ruleset for the platform.
// The SDK (sdk/observe-sdk.js) ships an identical copy of this ruleset so
// that redaction happens client-side, inside the monitored app's own
// process, before anything crosses the network boundary (per the Secure
// Telemetry requirement). The platform re-runs the same engine server-side
// as defense-in-depth in case a caller sends raw ingest traffic directly.

const DETECTOR_VERSION = 'v1.0.0-regex';

const ENTITY_PATTERNS = [
  { type: 'SIN', re: /\b\d{3}[ -]?\d{3}[ -]?\d{3}\b/g },
  { type: 'CREDIT_CARD', re: /\b(?:\d[ -]?){13,16}\b/g },
  { type: 'EMAIL', re: /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g },
  { type: 'PHONE', re: /\(?\b\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b/g },
  { type: 'DOB', re: /\b\d{4}-\d{2}-\d{2}\b/g },
  { type: 'ADDRESS', re: /\b\d{1,5}\s+([A-Z][a-z]+\s){1,3}(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b/g },
  // lightweight "NER-style" name catch: two capitalized words in a row,
  // excluding common sentence starters. A real deployment would swap this
  // for a proper NER microservice; kept as regex here per the recommended
  // "regex + rules engine" scope.
  { type: 'NAME', re: /\b(?!Please|Customer|Patient|Contact|His|Her|Can|The|Order|Dear|Hello|Hi|Thanks|Regards)[A-Z][a-z]+ [A-Z][a-z]+\b/g },
];

const POLICY_VERSION = 'redaction-policy-v3';

/**
 * Runs the hybrid detector over raw text.
 * Returns { redactedText, entities: [{ type }] } — never returns the raw
 * matched values, so callers can safely log/forward the result.
 */
function detectAndRedact(text) {
  if (!text || typeof text !== 'string') {
    return { redactedText: text || '', entities: [] };
  }

  let matches = [];
  ENTITY_PATTERNS.forEach(({ type, re }) => {
    const localRe = new RegExp(re.source, re.flags);
    let m;
    while ((m = localRe.exec(text)) !== null) {
      matches.push({ type, start: m.index, end: m.index + m[0].length });
      if (m.index === localRe.lastIndex) localRe.lastIndex++;
    }
  });

  matches.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
  const filtered = [];
  let lastEnd = -1;
  for (const m of matches) {
    if (m.start >= lastEnd) {
      filtered.push(m);
      lastEnd = m.end;
    }
  }

  let redactedText = '';
  let cursor = 0;
  filtered.forEach((m) => {
    redactedText += text.slice(cursor, m.start);
    redactedText += `[REDACTED:${m.type}]`;
    cursor = m.end;
  });
  redactedText += text.slice(cursor);

  return {
    redactedText,
    entities: filtered.map((m) => ({ type: m.type })),
    policyVersion: POLICY_VERSION,
    detectorVersion: DETECTOR_VERSION,
  };
}

module.exports = { detectAndRedact, ENTITY_PATTERNS, POLICY_VERSION, DETECTOR_VERSION };
