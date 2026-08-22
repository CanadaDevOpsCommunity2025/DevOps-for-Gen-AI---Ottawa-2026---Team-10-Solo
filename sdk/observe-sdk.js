'use strict';

// ---------------------------------------------------------------
// Sentinel Observe SDK
//
// Drop this into any app an AI agent builds. It is the mechanism that
// makes observability mandatory rather than optional: instead of relying
// on a prompt to remember to ask for PII masking, audit trails, access
// logging, etc., the agent is instructed (see AGENT_MANDATE.md at the repo
// root) to wire every LLM call and every privileged action through this
// SDK. Redaction happens HERE, inside the host app's own process — the
// Sentinel platform never receives raw prompt/response text, only redacted
// text plus entity-type metadata.
// ---------------------------------------------------------------

// Keep this ruleset identical to platform/src/redact.js. Duplicated
// on purpose: the whole point is that redaction must run inside the
// monitored app's process, before any network call, not as a step the
// platform performs after receiving raw data.
const ENTITY_PATTERNS = [
  { type: 'SIN', re: /\b\d{3}[ -]?\d{3}[ -]?\d{3}\b/g },
  { type: 'CREDIT_CARD', re: /\b(?:\d[ -]?){13,16}\b/g },
  { type: 'EMAIL', re: /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g },
  { type: 'PHONE', re: /\(?\b\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b/g },
  { type: 'DOB', re: /\b\d{4}-\d{2}-\d{2}\b/g },
  { type: 'ADDRESS', re: /\b\d{1,5}\s+([A-Z][a-z]+\s){1,3}(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr)\b/g },
  { type: 'NAME', re: /\b(?!Please|Customer|Patient|Contact|His|Her|Can|The|Order|Dear|Hello|Hi|Thanks|Regards)[A-Z][a-z]+ [A-Z][a-z]+\b/g },
];
const POLICY_VERSION = 'redaction-policy-v3';

function redact(text) {
  if (!text || typeof text !== 'string') return { redactedText: text || '', entities: [] };

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
  for (const m of matches) if (m.start >= lastEnd) { filtered.push(m); lastEnd = m.end; }

  let redactedText = '';
  let cursor = 0;
  filtered.forEach((m) => {
    redactedText += text.slice(cursor, m.start);
    redactedText += `[REDACTED:${m.type}]`;
    cursor = m.end;
  });
  redactedText += text.slice(cursor);

  return { redactedText, entities: filtered.map((m) => ({ type: m.type })) };
}

class ObserveClient {
  /**
   * @param {object} opts
   * @param {string} opts.apiBase   e.g. "http://localhost:4000"
   * @param {string} opts.appId     app id returned at registration
   * @param {string} opts.ingestKey ingest key returned at registration
   */
  constructor({ apiBase, appId, ingestKey }) {
    if (!apiBase || !appId || !ingestKey) {
      throw new Error('ObserveClient requires apiBase, appId, and ingestKey');
    }
    this.apiBase = apiBase.replace(/\/$/, '');
    this.appId = appId;
    this.ingestKey = ingestKey;
  }

  async _post(path, body) {
    try {
      const res = await fetch(`${this.apiBase}${path}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-app-id': this.appId,
          'x-ingest-key': this.ingestKey,
        },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        console.error(`[observe-sdk] ${path} failed: ${err.error || res.status}`);
        return null;
      }
      return await res.json();
    } catch (err) {
      // Fail-open by default so a platform outage doesn't take down the
      // host app; flip to fail-closed (rethrow) for high-sensitivity apps.
      console.error(`[observe-sdk] ${path} unreachable: ${err.message}`);
      return null;
    }
  }

  /**
   * Wrap every LLM prompt/response (or retrieved RAG chunk, tool-call
   * arg/result, etc.) through this before it touches a log line, a metric,
   * or a dashboard. Returns the redacted text — use THAT for anything you
   * display, log, or forward; never the raw input.
   */
  async observeLLMCall({ field, rawText }) {
    const { redactedText, entities } = redact(rawText);
    await this._post('/api/ingest/telemetry', { field, redactedText, entities });
    return redactedText;
  }

  /** Call on every sign-in, privilege grant, config change, data export, etc. */
  async logAccess({ userEmail, action, result, sourceIp }) {
    return this._post('/api/ingest/access', { userEmail, action, result, sourceIp });
  }

  /**
   * Call for any domain-significant event. Use severity 'high' or
   * 'critical' for anything that should page a human — Sentinel routes
   * those to the platform admin and this app's registered owner.
   */
  async logAudit({ eventType, severity, detail, actor }) {
    return this._post('/api/ingest/audit', { eventType, severity, detail, actor });
  }

  /** Report an inbound/outbound data exchange with another system or app. */
  async logDataFlow({ direction, counterparty, counterpartyAppId, dataCategories, note }) {
    return this._post('/api/ingest/dataflow', { direction, counterparty, counterpartyAppId, dataCategories, note });
  }
}

module.exports = { ObserveClient, redact, POLICY_VERSION };
