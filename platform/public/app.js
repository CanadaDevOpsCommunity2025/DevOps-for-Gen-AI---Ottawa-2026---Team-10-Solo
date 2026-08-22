'use strict';

// ---------------------------------------------------------------
// Sentinel dashboard — talks to the real Sentinel API. No more
// hardcoded sample arrays: every view below is populated from
// SQLite via fetch() calls, scoped by the signed-in user's tenant
// and role (enforced server-side, not just hidden in the UI).
// ---------------------------------------------------------------

const token = localStorage.getItem('sentinel_token');
const user = JSON.parse(localStorage.getItem('sentinel_user') || 'null');
if (!token || !user) {
  window.location.href = 'index.html';
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) {
    localStorage.removeItem('sentinel_token');
    localStorage.removeItem('sentinel_user');
    window.location.href = 'index.html';
    throw new Error('unauthorized');
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `request failed: ${path}`);
  return data;
}

const state = {
  view: 'overview',
  tenant: user.role === 'app_owner' ? user.tenant_id : 'all',
  tenants: [],
  frTenant: null,
  traceExpanded: null,
};
const frTraceCache = {};

function tenantQS() {
  return state.tenant && state.tenant !== 'all' ? `?tenant=${encodeURIComponent(state.tenant)}` : '';
}

function sevBadge(sev) {
  return `<span class="badge ${sev}">${sev}</span>`;
}
function statusBadge(status) {
  return `<span class="badge ${status}">${status.replace('_', ' ')}</span>`;
}
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function fmtTs(ts) {
  return (ts || '').replace('T', ' ').slice(0, 19);
}

let appNameCache = {};
async function loadAppNames() {
  const apps = await api(`/api/apps${tenantQS()}`);
  appNameCache = {};
  apps.forEach((a) => (appNameCache[a.id] = a.name));
  return apps;
}
function appName(id) {
  return appNameCache[id] || id;
}

// ---------------- TENANT SWITCHER ----------------
async function renderTenantSwitcher() {
  const el = document.getElementById('tenantSwitcher');
  if (user.role === 'app_owner') {
    el.innerHTML = `
      <label>Signed in as</label>
      <div class="who"><strong>${escapeHtml(user.email)}</strong><br>App owner · scoped to your tenant only</div>
    `;
    return;
  }

  if (!state.tenants.length) {
    state.tenants = await api('/api/tenants');
  }
  el.innerHTML = `
    <label>Viewing as</label>
    <div class="who" style="margin-bottom:8px;">${escapeHtml(user.email)} · ${user.role.replace('_', ' ')}</div>
    <select id="tenantSelect">
      <option value="all">All Tenants (Admin)</option>
      ${state.tenants.map((t) => `<option value="${t.id}" ${state.tenant === t.id ? 'selected' : ''}>${escapeHtml(t.name)}</option>`).join('')}
    </select>
  `;
  document.getElementById('tenantSelect').addEventListener('change', (e) => {
    state.tenant = e.target.value;
    renderCurrentView();
  });
}

// ---------------- OVERVIEW ----------------
async function renderOverview() {
  const [overview] = await Promise.all([api(`/api/overview${tenantQS()}`), loadAppNames()]);

  return `
  <div class="grid grid-4 section">
    <div class="card stat-card ${overview.criticalCount ? 'bad' : 'good'}">
      <div class="stat-value">${overview.criticalCount}</div>
      <div class="stat-label">Critical alerts open</div>
    </div>
    <div class="card stat-card ${overview.highCount ? 'warn' : 'good'}">
      <div class="stat-value">${overview.highCount}</div>
      <div class="stat-label">High-severity events</div>
    </div>
    <div class="card stat-card good">
      <div class="stat-value">${overview.appCount}</div>
      <div class="stat-label">Monitored applications</div>
    </div>
    <div class="card stat-card ${overview.flaggedFlowCount ? 'bad' : 'good'}">
      <div class="stat-value">${overview.flaggedFlowCount}</div>
      <div class="stat-label">Cross-tenant leak flags</div>
    </div>
  </div>

  <div class="two-col section">
    <div class="card">
      <div class="card-title">Monitored applications</div>
      ${overview.apps.length ? `<table>
        <thead><tr><th>App</th><th>Region</th><th>Retention</th><th>Owner</th><th>Status</th></tr></thead>
        <tbody>
          ${overview.apps.map((a) => `
            <tr>
              <td>${escapeHtml(a.name)}</td>
              <td class="mono">${a.region}</td>
              <td class="mono">${a.retention_days}d</td>
              <td class="faint">${escapeHtml(a.owner_email)}</td>
              <td>${a.drift ? '<span class="badge high">drift</span>' : '<span class="badge ok">aligned</span>'}</td>
            </tr>`).join('')}
        </tbody>
      </table>` : `<div class="empty-note">No applications registered for this tenant yet. Run flight-recorder/register_with_sentinel.py to onboard one.</div>`}
    </div>
    <div class="card">
      <div class="card-title">Latest audit signals</div>
      ${overview.latestAudits.length ? overview.latestAudits.map((e) => `
        <div style="padding:9px 0; border-bottom:1px solid var(--line-soft);">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span class="mono" style="font-size:12px;">${escapeHtml(e.event_type)}</span>
            ${sevBadge(e.severity)}
          </div>
          <div class="dim" style="font-size:12px; margin-top:4px;">${escapeHtml(appName(e.app_id))} · ${fmtTs(e.created_at)}</div>
        </div>
      `).join('') : `<div class="empty-note">No audit events yet.</div>`}
    </div>
  </div>

  <div class="card">
    <div class="card-title">Compliance posture snapshot</div>
    <div class="grid grid-3">
      ${overview.apps.map((a) => `<div style="border:1px solid var(--line); border-radius:8px; padding:12px;">
          <div style="display:flex; justify-content:space-between; width:100%;">
            <strong style="font-size:13px;">${escapeHtml(a.name)}</strong>
            ${a.drift ? '<span class="badge high">drift</span>' : '<span class="badge ok">aligned</span>'}
          </div>
          <div class="dim" style="font-size:12px; margin-top:4px;">Applicable: ${a.applicable_regulations.join(', ') || '—'}</div>
          <div class="faint" style="font-size:11.5px; margin-top:2px;">Declared PII: ${a.declared_pii.join(', ') || 'none declared'}</div>
        </div>`).join('') || `<div class="empty-note">No applications yet.</div>`}
    </div>
  </div>
  `;
}

// ---------------- PII REDACTION ----------------
const ENTITY_PATTERNS = [
  { type: 'SIN', re: /\b\d{3}[ -]?\d{3}[ -]?\d{3}\b/g },
  { type: 'CREDIT_CARD', re: /\b(?:\d[ -]?){13,16}\b/g },
  { type: 'EMAIL', re: /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/g },
  { type: 'PHONE', re: /\(?\b\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b/g },
  { type: 'DOB', re: /\b\d{4}-\d{2}-\d{2}\b/g },
  { type: 'NAME', re: /\b(?!Please|Customer|Patient|Contact|His|Her|Can)[A-Z][a-z]+ [A-Z][a-z]+\b/g },
];
const SAMPLE_PROMPTS = [
`Customer Jordan Blake (jordan.blake@gmail.com) is asking about order #88213.
His SIN is 046 454 286 and he wants to update the card on file to 4539 1488 0343 6467.
Can you confirm the refund status and call him back at (613) 555-0142?`,
`Patient Maria Alvarez, DOB 1984-03-02, presented with elevated glucose (312 mg/dL).
Contact: maria.alvarez84@outlook.com. Please summarize for the referring physician.`,
];
function detectAndRedact(text) {
  let matches = [];
  ENTITY_PATTERNS.forEach(({ type, re }) => {
    const localRe = new RegExp(re.source, re.flags);
    let m;
    while ((m = localRe.exec(text)) !== null) matches.push({ type, value: m[0], start: m.index, end: m.index + m[0].length });
  });
  matches.sort((a, b) => a.start - b.start || (b.end - b.start) - (a.end - a.start));
  const filtered = [];
  let lastEnd = -1;
  for (const m of matches) if (m.start >= lastEnd) { filtered.push(m); lastEnd = m.end; }
  return { entities: filtered };
}
function renderRawHighlighted(text, entities) {
  let html = '', cursor = 0;
  entities.forEach((m) => { html += escapeHtml(text.slice(cursor, m.start)); html += `<span class="pii-strike" title="${m.type}">${escapeHtml(m.value)}</span>`; cursor = m.end; });
  html += escapeHtml(text.slice(cursor));
  return html.replace(/\n/g, '<br>');
}
function renderRedactedHtml(text, entities) {
  let html = '', cursor = 0;
  entities.forEach((m) => { html += escapeHtml(text.slice(cursor, m.start)); html += `<span class="pii-token">[REDACTED:${m.type}]</span>`; cursor = m.end; });
  html += escapeHtml(text.slice(cursor));
  return html.replace(/\n/g, '<br>');
}
window.loadSample = function (idx) {
  const sel = document.getElementById('sampleSelect');
  if (sel) sel.value = idx;
  document.getElementById('rawInput').value = SAMPLE_PROMPTS[idx];
  window.runRedactionDemo();
};
window.runRedactionDemo = function () {
  const text = document.getElementById('rawInput').value;
  const { entities } = detectAndRedact(text);
  document.getElementById('rawPanel').innerHTML = renderRawHighlighted(text, entities);
  document.getElementById('redactedPanel').innerHTML = renderRedactedHtml(text, entities);
  const counts = {};
  entities.forEach((e) => (counts[e.type] = (counts[e.type] || 0) + 1));
  const legend = Object.entries(counts).map(([t, c]) => `<span class="badge info">${t} × ${c}</span>`).join(' ') || `<span class="badge neutral">no sensitive entities detected</span>`;
  document.getElementById('entityLegend').innerHTML = legend;
};

async function renderRedaction() {
  const [events] = await Promise.all([api(`/api/redaction-events${tenantQS()}`), loadAppNames()]);
  return `
  <div class="section">
    <div class="redact-demo">
      <div class="redact-demo-head">
        <div class="title">CLIENT-SIDE PREVIEW — same ruleset the SDK runs before data leaves the app</div>
        <span class="badge neutral">regex + rules engine</span>
      </div>
      <div style="padding:16px 18px 0;">
        <textarea id="rawInput">${SAMPLE_PROMPTS[0]}</textarea>
        <div style="display:flex; gap:8px; align-items:center;">
          <button class="run-btn" onclick="runRedactionDemo()">Run detector →</button>
          <select id="sampleSelect" onchange="loadSample(this.value)" style="background:var(--bg); color:var(--text-dim); border:1px solid var(--line); border-radius:6px; padding:7px 8px; font-size:12px; margin-top:10px;">
            <option value="0">Sample: support ticket</option>
            <option value="1">Sample: clinical note</option>
          </select>
        </div>
      </div>
      <div class="redact-panels" style="margin-top:10px;">
        <div class="redact-panel"><div class="label">Raw payload (never leaves the app boundary)</div><div id="rawPanel">—</div></div>
        <div class="redact-panel"><div class="label">What reaches telemetry / logs / dashboard</div><div id="redactedPanel">—</div></div>
      </div>
      <div class="entity-legend" id="entityLegend"></div>
    </div>
  </div>

  <div class="card section">
    <div class="card-title">Real redaction events (from live application traffic)</div>
    ${events.length ? `<table>
      <thead><tr><th>App</th><th>Field</th><th>Entity</th><th>Action</th><th>Policy ver.</th><th>Time</th></tr></thead>
      <tbody>
        ${events.map((r) => `<tr>
            <td>${escapeHtml(appName(r.app_id))}</td>
            <td class="mono">${escapeHtml(r.field)}</td>
            <td><span class="badge info">${r.entity_type}</span></td>
            <td class="dim">${r.action}</td>
            <td class="mono faint">${escapeHtml(r.policy_version)}</td>
            <td class="mono faint">${fmtTs(r.created_at)}</td>
          </tr>`).join('')}
      </tbody>
    </table>` : `<div class="empty-note">No redaction events yet — run flight-recorder's traffic generator with --sink sqlite,sentinel to populate this table.</div>`}
  </div>

  <div class="card">
    <div class="card-title">Failure policy</div>
    <div class="flow-row"><div class="flow-node">Detector reachable</div><span class="flow-arrow">→</span><div class="flow-node" style="color:var(--teal);">redact & forward</div></div>
    <div class="flow-row"><div class="flow-node">Detector unreachable</div><span class="flow-arrow">→</span><div class="flow-node" style="color:var(--red);">fail-closed: drop span</div></div>
    <p class="small-note">Redaction runs inside the SDK, in the monitored app's own process — the platform never receives raw text, only redacted text plus entity-type metadata.</p>
  </div>
  `;
}

// ---------------- AUDIT TRAIL ----------------
async function renderAudit() {
  const [audits, alerts] = await Promise.all([api(`/api/audit-events${tenantQS()}`), api(`/api/alerts${tenantQS()}`)]);
  await loadAppNames();
  return `
  <div class="card section">
    <div class="card-title">Audit trail — all applications (append-only)</div>
    ${audits.length ? `<table>
      <thead><tr><th>App</th><th>Type</th><th>Severity</th><th>Detail</th><th>Timestamp</th><th>Notified</th></tr></thead>
      <tbody>
        ${audits.map((e) => `
          <tr>
            <td>${escapeHtml(appName(e.app_id))}</td>
            <td class="mono">${escapeHtml(e.event_type)}</td>
            <td>${sevBadge(e.severity)}</td>
            <td class="dim" style="max-width:320px;">${escapeHtml(e.detail)}</td>
            <td class="mono faint">${fmtTs(e.created_at)}</td>
            <td>${(e.severity === 'critical' || e.severity === 'high') ? '<span class="badge ok">admin + owner</span>' : '<span class="badge neutral">—</span>'}</td>
          </tr>`).join('')}
      </tbody>
    </table>` : `<div class="empty-note">No audit events recorded yet.</div>`}
  </div>
  <div class="card section">
    <div class="card-title">Alert feed (routed on high/critical)</div>
    ${alerts.length ? alerts.map((a) => `
      <div style="padding:9px 0; border-bottom:1px solid var(--line-soft);">
        <div style="display:flex; justify-content:space-between;">
          ${sevBadge(a.severity)}
          <span class="mono faint" style="font-size:11px;">${fmtTs(a.created_at)}</span>
        </div>
        <div style="font-size:12.5px; margin-top:4px;">${escapeHtml(a.message)}</div>
        <div class="faint" style="font-size:11px; margin-top:2px;">→ ${JSON.parse(a.recipients).join(', ')}</div>
      </div>
    `).join('') : `<div class="empty-note">No alerts routed yet.</div>`}
  </div>
  <div class="card">
    <div class="card-title">Alert routing rule</div>
    <p class="small-note" style="font-size:12.5px; color:var(--text-dim);">
      When an audit event's severity is <strong>high</strong> or <strong>critical</strong>, Sentinel notifies
      the platform administrator and the registered owner of the emitting application, and records the delivery
      in the alert feed above.
    </p>
  </div>
  `;
}

// ---------------- ACCESS LOGS ----------------
async function renderAccess() {
  const [events, apps] = await Promise.all([api(`/api/access-events${tenantQS()}`), loadAppNames()]);
  return `
  <div class="two-col section">
    <div class="card">
      <div class="card-title">Recent sign-ins &amp; privileged actions</div>
      ${events.length ? `<table>
        <thead><tr><th>App</th><th>User</th><th>Action</th><th>Result</th><th>Time</th></tr></thead>
        <tbody>
          ${events.map((e) => `
            <tr>
              <td>${escapeHtml(appName(e.app_id))}</td>
              <td class="mono" style="font-size:11.5px;">${escapeHtml(e.user_email)}</td>
              <td class="dim">${escapeHtml(e.action)}</td>
              <td>${e.result.includes('success') ? '<span class="badge ok">success</span>' : e.result.includes('blocked') ? '<span class="badge high">blocked</span>' : '<span class="badge critical">failed</span>'}</td>
              <td class="mono faint">${fmtTs(e.created_at)}</td>
            </tr>`).join('')}
        </tbody>
      </table>` : `<div class="empty-note">No access events yet.</div>`}
    </div>
    <div class="card">
      <div class="card-title">Log retention &amp; purge policy per app</div>
      ${apps.map((a) => `
        <div style="padding:10px 0; border-bottom:1px solid var(--line-soft);">
          <div style="display:flex; justify-content:space-between;">
            <strong style="font-size:13px;">${escapeHtml(a.name)}</strong>
            <span class="mono faint" style="font-size:11.5px;">${a.retention_days} days</span>
          </div>
          <div class="progress-track"><div class="progress-fill" style="width:${Math.min(100, a.retention_days / 2)}%;"></div></div>
          <div class="small-note">Purge before floor: <strong style="color:var(--red);">not permitted</strong> · Deletion allowed at all: <strong style="${a.deletion_allowed ? 'color:var(--teal);' : 'color:var(--red);'}">${a.deletion_allowed ? 'yes' : 'no'}</strong></div>
        </div>
      `).join('') || `<div class="empty-note">No applications yet.</div>`}
    </div>
  </div>
  `;
}

// ---------------- LIVE RAG TRACES (bridged from flight-recorder) ----------------
const FR_SCENARIOS = ['nominal', 'retrieval_miss', 'tool_loop', 'cost_spike', 'pii_leak', 'timeout', 'prompt_injection', 'rate_limit', 'latency_spike'];

async function frTenantsFor() {
  const qs = state.tenant && state.tenant !== 'all' ? `?tenant=${encodeURIComponent(state.tenant)}` : '';
  const resp = await api(`/api/fr/tenants${qs}`);
  return resp.tenants || [];
}

function frVerdictBadge(v) {
  if (!v) return sevBadge('info');
  const sev = v.severity === 'critical' ? 'critical' : v.severity === 'warning' ? 'high' : 'info';
  return sevBadge(sev);
}

function frTraceDetailHtml(detail) {
  if (!detail) return `<div class="empty-note">Loading…</div>`;
  if (detail.error) return `<div class="empty-note">Failed to load: ${escapeHtml(detail.error)}</div>`;
  const v = detail.verdict || {};
  const spans = detail.spans || [];
  const payloads = detail.payloads || [];
  return `
    <div style="padding:10px 4px;">
      <div style="display:flex; gap:8px; align-items:center; margin-bottom:8px;">
        ${frVerdictBadge(v)}<strong style="font-size:12.5px;">${escapeHtml(v.title || 'nominal')}</strong>
      </div>
      ${v.detail ? `<div class="dim" style="font-size:12px; margin-bottom:10px;">${escapeHtml(v.detail)}</div>` : ''}
      <table style="margin-bottom:10px;">
        <thead><tr><th>Kind</th><th>Name</th><th>Status</th><th>Duration</th></tr></thead>
        <tbody>
          ${spans.map((s) => `<tr>
              <td class="mono faint">${escapeHtml(s.kind)}</td>
              <td>${escapeHtml(s.name || '')}</td>
              <td>${s.status === 'error' ? '<span class="badge critical">error</span>' : '<span class="badge ok">ok</span>'}</td>
              <td class="mono faint">${Math.round(s.duration_ms || 0)}ms</td>
            </tr>`).join('')}
        </tbody>
      </table>
      ${payloads.length ? `
      <div class="faint" style="font-size:11px; text-transform:uppercase; letter-spacing:.06em; margin-bottom:6px;">Payloads (post-redaction — this is all Sentinel or flight-recorder ever shows)</div>
      ${payloads.map((p) => `
        <div style="border:1px solid var(--line); border-radius:6px; padding:8px 10px; margin-bottom:6px;">
          <div style="display:flex; justify-content:space-between;">
            <span class="mono faint" style="font-size:11px;">${escapeHtml(p.role)}</span>
            ${p.masked_here ? '<span class="badge high">masked here (defence-in-depth catch)</span>' : (p.caught_at === 'ingest' ? '<span class="badge ok">masked at ingest</span>' : '<span class="badge neutral">clean</span>')}
          </div>
          <div class="dim" style="font-size:12px; margin-top:4px; white-space:pre-wrap;">${escapeHtml(p.content)}</div>
        </div>
      `).join('')}` : ''}
    </div>
  `;
}

function frTraceRow(t) {
  const id = t.trace_id;
  const expanded = state.traceExpanded === id;
  const ts = Number(t.start_ns) ? fmtTs(new Date(Number(t.start_ns) / 1e6).toISOString()) : '—';
  const row = `
    <tr class="fr-trace-row" data-trace="${escapeHtml(id)}" style="cursor:pointer;">
      <td class="faint">${expanded ? '▾' : '▸'}</td>
      <td class="mono" style="font-size:11px;">${escapeHtml(String(id).slice(0, 12))}…</td>
      <td><span class="badge info">${escapeHtml(String(t.scenario || 'trace').replace(/_/g, ' '))}</span></td>
      <td>${t.status === 'error' ? '<span class="badge critical">error</span>' : '<span class="badge ok">ok</span>'}</td>
      <td class="mono faint">${t.span_count || '—'}</td>
      <td class="mono">$${Number(t.cost_usd || 0).toFixed(4)}</td>
      <td class="mono faint">${Math.round(t.duration_ms || 0)}ms</td>
      <td class="mono faint">${ts}</td>
    </tr>`;
  if (!expanded) return row;
  const detail = frTraceCache[`${state.frTenant}:${id}`];
  return `${row}<tr><td colspan="8">${frTraceDetailHtml(detail)}</td></tr>`;
}

async function renderTraces() {
  let frTenants;
  try {
    frTenants = await frTenantsFor();
  } catch (err) {
    return `<div class="empty-note">Could not reach flight-recorder bridge: ${escapeHtml(err.message)}</div>`;
  }
  if (!frTenants.length) {
    return `<div class="empty-note">No flight-recorder-connected application for this tenant. Run flight-recorder/register_with_sentinel.py to connect one.</div>`;
  }
  if (!state.frTenant || !frTenants.some((t) => t.sentinelTenantId === state.frTenant)) {
    state.frTenant = frTenants[0].sentinelTenantId;
  }
  const frQS = `?tenant=${encodeURIComponent(state.frTenant)}`;

  let stats, tracesResp;
  try {
    [stats, tracesResp] = await Promise.all([
      api(`/api/fr/stats${frQS}`),
      api(`/api/fr/traces${frQS}&limit=40`),
    ]);
  } catch (err) {
    return `<div class="empty-note">Could not reach flight-recorder: ${escapeHtml(err.message)}</div>`;
  }
  const traces = tracesResp.traces || [];

  const tenantPicker = frTenants.length > 1 ? `
    <select id="frTenantSelect" style="background:var(--bg); color:var(--text-dim); border:1px solid var(--line); border-radius:6px; padding:5px 8px; font-size:12px;">
      ${frTenants.map((t) => `<option value="${t.sentinelTenantId}" ${state.frTenant === t.sentinelTenantId ? 'selected' : ''}>${escapeHtml(t.frTenant)}</option>`).join('')}
    </select>` : `<span class="dim mono" style="font-size:12px;">${escapeHtml(frTenants[0].frTenant)}</span>`;

  return `
  <div class="grid grid-4 section">
    <div class="card stat-card good"><div class="stat-value">${stats.traces || 0}</div><div class="stat-label">Traces recorded</div></div>
    <div class="card stat-card ${stats.errors ? 'warn' : 'good'}"><div class="stat-value">${stats.errors || 0}</div><div class="stat-label">Errored traces</div></div>
    <div class="card stat-card ${stats.escaped ? 'bad' : 'good'}"><div class="stat-value">${stats.escaped || 0}</div><div class="stat-label">PII escaped ingest</div></div>
    <div class="card stat-card good"><div class="stat-value">$${Number(stats.cost_usd || 0).toFixed(4)}</div><div class="stat-label">Total cost tracked</div></div>
  </div>

  <div class="card section">
    <div class="card-title" style="display:flex; justify-content:space-between; align-items:center; text-transform:none; letter-spacing:normal;">
      <span style="text-transform:uppercase; letter-spacing:.06em;">Live traces from flight-recorder</span>
      ${tenantPicker}
    </div>
    <div style="display:flex; gap:8px; align-items:center; margin-bottom:12px; flex-wrap:wrap;">
      <select id="frScenarioSelect" style="background:var(--bg); color:var(--text-dim); border:1px solid var(--line); border-radius:6px; padding:7px 8px; font-size:12px;">
        ${FR_SCENARIOS.map((s) => `<option value="${s}">${s.replace(/_/g, ' ')}</option>`).join('')}
      </select>
      <button class="run-btn" id="frInjectBtn">Fire live scenario →</button>
      <span class="faint" style="font-size:11.5px;">Generates real RAG traffic through flight-recorder — lands here <em>and</em> in flight-recorder's own console at once.</span>
    </div>
    ${traces.length ? `<table>
      <thead><tr><th></th><th>Trace</th><th>Scenario</th><th>Status</th><th>Spans</th><th>Cost</th><th>Duration</th><th>Time</th></tr></thead>
      <tbody id="frTraceBody">
        ${traces.map((t) => frTraceRow(t)).join('')}
      </tbody>
    </table>` : `<div class="empty-note">No traces yet for this app. Fire a live scenario above, or run flight-recorder's traffic generator.</div>`}
  </div>
  `;
}

function wireTraces() {
  const btn = document.getElementById('frInjectBtn');
  if (btn) btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Firing…';
    const scenario = document.getElementById('frScenarioSelect').value;
    try {
      await api('/api/fr/inject', { method: 'POST', body: JSON.stringify({ scenario, count: 5 }) });
    } catch (err) {
      // still refresh below — flight-recorder may have partially run
    }
    await renderCurrentView();
  });

  const sel = document.getElementById('frTenantSelect');
  if (sel) sel.addEventListener('change', (e) => {
    state.frTenant = e.target.value;
    state.traceExpanded = null;
    renderCurrentView();
  });

  document.querySelectorAll('.fr-trace-row').forEach((row) => {
    row.addEventListener('click', async () => {
      const id = row.dataset.trace;
      if (state.traceExpanded === id) {
        state.traceExpanded = null;
        renderCurrentView();
        return;
      }
      const key = `${state.frTenant}:${id}`;
      if (!frTraceCache[key]) {
        try {
          const [traceResp, payloadsResp] = await Promise.all([
            api(`/api/fr/trace/${encodeURIComponent(id)}?tenant=${encodeURIComponent(state.frTenant)}`),
            api(`/api/fr/payloads/${encodeURIComponent(id)}?tenant=${encodeURIComponent(state.frTenant)}`),
          ]);
          frTraceCache[key] = { ...traceResp, payloads: payloadsResp.payloads || [] };
        } catch (err) {
          frTraceCache[key] = { error: err.message };
        }
      }
      state.traceExpanded = id;
      renderCurrentView();
    });
  });
}

// ---------------- RETENTION & REGION ----------------
async function renderRetention() {
  const data = await api(`/api/retention${tenantQS()}`);
  return `
  <div class="card section">
    <div class="card-title">Data residency &amp; retention by application</div>
    ${data.apps.length ? `<table>
      <thead><tr><th>App</th><th>Serves region</th><th>Applicable law</th><th>Retention set</th><th>Status</th></tr></thead>
      <tbody>
        ${data.apps.map((a) => `<tr>
            <td>${escapeHtml(a.name)}</td>
            <td class="mono">${a.region}</td>
            <td class="dim">${a.applicable_regulations.join(', ')}</td>
            <td class="mono">${a.retention_days}d</td>
            <td>${statusBadge(a.status)}</td>
          </tr>
          ${a.reasons.length ? `<tr><td colspan="5" class="small-note" style="padding-left:10px;">↳ ${a.reasons.join('; ')}</td></tr>` : ''}`).join('')}
      </tbody>
    </table>` : `<div class="empty-note">No applications yet.</div>`}
    <p class="small-note">Retention floors are cross-checked against a GDPR storage-limitation heuristic via the "Run compliance sweep" action (admin/compliance only) — simulating the nightly enforcement job.</p>
  </div>

  <div class="card">
    <div class="card-title">Sensitive-category → regulation reference</div>
    <div class="grid grid-3">
      ${Object.entries(data.piiRegulationMap).map(([k, v]) => `
        <div style="border:1px solid var(--line); border-radius:8px; padding:10px;">
          <div class="mono" style="color:var(--teal); font-size:12px;">${k}</div>
          <div class="dim" style="font-size:11.5px; margin-top:4px;">${v}</div>
        </div>
      `).join('')}
    </div>
  </div>
  `;
}

// ---------------- TENANT ISOLATION ----------------
async function renderTenancy() {
  const [flows] = await Promise.all([api(`/api/dataflows${tenantQS()}`), loadAppNames()]);
  return `
  <div class="card section">
    <div class="card-title">Inter-application data flow (inbound / outbound)</div>
    ${flows.length ? `<table>
      <thead><tr><th>Source</th><th></th><th>Destination</th><th>Direction</th><th>Data exchanged</th></tr></thead>
      <tbody>
        ${flows.map((f) => `
          <tr style="${f.flagged ? 'background:rgba(239,91,91,.06);' : ''}">
            <td>${escapeHtml(appName(f.app_id))}</td>
            <td class="faint">→</td>
            <td>${escapeHtml(f.counterparty_app_id ? appName(f.counterparty_app_id) : f.counterparty)}</td>
            <td>${f.flagged ? '<span class="badge critical">flagged</span>' : `<span class="badge neutral">${f.direction}</span>`}</td>
            <td class="mono" style="font-size:11.5px;">${JSON.parse(f.data_categories).join(', ')}</td>
          </tr>
          ${f.note ? `<tr><td colspan="5" class="small-note" style="padding-left:10px; color:${f.flagged ? 'var(--red)' : 'var(--text-faint)'};">↳ ${escapeHtml(f.note)}</td></tr>` : ''}
        `).join('')}
      </tbody>
    </table>` : `<div class="empty-note">No inter-application data flows reported yet.</div>`}
  </div>

  <div class="card">
    <div class="card-title">Enforcement model</div>
    <div class="flow-row"><div class="flow-node">Every event ingested</div><span class="flow-arrow">→</span><div class="flow-node">tagged with tenant_id</div><span class="flow-arrow">→</span><div class="flow-node" style="color:var(--teal);">query layer filters by requester's tenant</div></div>
    <p class="small-note">A platform admin role may cross tenant boundaries only via an explicit <code>?tenant=</code> query — every such cross-tenant read is itself logged as an access event (see Access Logs).</p>
  </div>
  `;
}

// ---------------- SECURE TELEMETRY ----------------
function renderTelemetry() {
  return `
  <div class="two-col section">
    <div class="card">
      <div class="card-title">Telemetry path</div>
      <div class="flow-row"><div class="flow-node">App + SDK</div><span class="flow-arrow">→</span><div class="flow-node" style="color:var(--teal);">redaction runs in-process</div></div>
      <div class="flow-row"><div class="flow-node">SDK</div><span class="flow-arrow">→ HTTPS/TLS →</span><div class="flow-node">Sentinel ingest API</div></div>
      <div class="flow-row"><div class="flow-node">Ingest</div><span class="flow-arrow">→</span><div class="flow-node">SQLite (demo) / KMS-encrypted store (production)</div></div>
      <p class="small-note">Redaction executes before the network boundary — unmasked payloads never leave the source application's process. In this reference build, transport is plain HTTP on localhost for demo convenience; production deployments terminate TLS at the ingest API.</p>
    </div>
    <div class="card">
      <div class="card-title">Transport &amp; storage controls</div>
      <table>
        <thead><tr><th>Control</th><th>Setting</th></tr></thead>
        <tbody>
          <tr><td>Auth on ingest endpoints</td><td class="mono">per-app ingest key (x-app-id / x-ingest-key)</td></tr>
          <tr><td>Auth on dashboard/API</td><td class="mono">JWT (HS256), 8h expiry, role + tenant claims</td></tr>
          <tr><td>Pre-transmission masking</td><td class="mono">SDK-side, before any network call</td></tr>
          <tr><td>Tenant scoping</td><td class="mono">enforced server-side on every read</td></tr>
          <tr><td>Recommended production transport</td><td class="mono">TLS 1.2+, AES-256 at rest, KMS-managed keys</td></tr>
        </tbody>
      </table>
    </div>
  </div>
  `;
}

// ---------------------------------------------------------------
// Controller
// ---------------------------------------------------------------

const VIEW_META = {
  overview: { title: 'Overview', sub: 'Real-time posture across all monitored AI applications' },
  redaction: { title: 'PII Redaction', sub: 'Sensitive-data detection and masking before telemetry leaves the app' },
  audit: { title: 'Audit Trail', sub: 'Structured audit events from every monitored application' },
  access: { title: 'Access-Controlled Logs', sub: 'Sign-ins, privileged actions, and purge attempts, centrally collected' },
  traces: { title: 'Live RAG Traces', sub: 'Real spans, verdicts, and redacted payloads pulled live from flight-recorder' },
  retention: { title: 'Retention & Region', sub: 'Data residency, retention floors, and applicable regulation' },
  tenancy: { title: 'Tenant Isolation', sub: 'Cross-application data flow and tenant boundary enforcement' },
  telemetry: { title: 'Secure Telemetry', sub: 'Transport and storage controls for all platform telemetry' },
};
const RENDERERS = {
  overview: renderOverview,
  redaction: renderRedaction,
  audit: renderAudit,
  access: renderAccess,
  traces: renderTraces,
  retention: renderRetention,
  tenancy: renderTenancy,
  telemetry: renderTelemetry,
};

async function renderCurrentView() {
  document.querySelectorAll('.view').forEach((v) => v.classList.add('hidden'));
  const el = document.getElementById('view-' + state.view);
  el.classList.remove('hidden');
  el.innerHTML = `<div class="empty-note">Loading…</div>`;
  try {
    el.innerHTML = await RENDERERS[state.view]();
  } catch (err) {
    el.innerHTML = `<div class="empty-note">Failed to load: ${escapeHtml(err.message)}</div>`;
  }

  document.getElementById('viewTitle').textContent = VIEW_META[state.view].title;
  document.getElementById('viewSubtitle').textContent = VIEW_META[state.view].sub;

  if (state.view === 'redaction') window.loadSample(document.getElementById('sampleSelect')?.value || '0');
  if (state.view === 'traces') wireTraces();

  document.getElementById('sweepBtn').style.display = user.role !== 'app_owner' ? 'inline-block' : 'none';

  updateAlertPill();
}

async function updateAlertPill() {
  const overview = await api(`/api/overview${tenantQS()}`);
  const active = overview.criticalCount + overview.highCount;
  const pill = document.getElementById('alertPill');
  pill.textContent = active + ' active alert' + (active === 1 ? '' : 's');
  pill.classList.toggle('clear', active === 0);
}

document.querySelectorAll('.nav-item').forEach((item) => {
  item.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach((i) => i.classList.remove('active'));
    item.classList.add('active');
    state.view = item.dataset.view;
    renderCurrentView();
  });
});

document.getElementById('logoutBtn').addEventListener('click', () => {
  localStorage.removeItem('sentinel_token');
  localStorage.removeItem('sentinel_user');
  window.location.href = 'index.html';
});

document.getElementById('sweepBtn').addEventListener('click', async () => {
  const btn = document.getElementById('sweepBtn');
  btn.textContent = 'Sweeping…';
  try {
    const result = await api('/api/compliance/sweep', { method: 'POST' });
    btn.textContent = `Swept ${result.appsChecked} app(s), ${result.newlyFlagged.length} newly flagged`;
  } catch (err) {
    btn.textContent = 'Sweep failed';
  }
  setTimeout(() => (btn.textContent = 'Run compliance sweep'), 2500);
  renderCurrentView();
});

renderTenantSwitcher().then(renderCurrentView);
