/* ══════════════════════════════════════════════════════════════════════════
   AuditIQ — Frontend Script
   ══════════════════════════════════════════════════════════════════════════ */

const API         ='http://172.19.0.34/api'; 
//const API         ='http://localhost:5000/api';
const STORAGE_KEY = 'auditiq_job_v2';

// ── State ──────────────────────────────────────────────────────────────────
let currentJobId     = null;
let currentJobStatus = 'idle';
let currentResults   = null;
let activeView       = 'audit';
let resultFilter     = 'all';
let allTickets       = [];

// ── Metric labels / max scores ─────────────────────────────────────────────
const METRIC_LABELS = {
  response_within_sla      : 'Response SLA',
  short_desc_quality       : 'Short Description Quality',
  priority_reassessed      : 'Priority Re-assessed',
  incident_reassigned      : 'Reassignment Documented',
  user_contact             : 'User Contact',
  pending_status           : 'Pending Status',
  work_notes_regular_update: 'Work Notes Updated Regularly',
  resolution_notes_quality : 'Resolution Notes Quality',
  resolution_sla           : 'Resolution SLA',
  user_confirmation        : 'User Confirmation',
  reopened_user_connect    : 'Contact After Reopen',
  kba_education            : 'KBA Shared',
};

const METRIC_MAX = {
  response_within_sla: 5, short_desc_quality: 5, priority_reassessed: 10,
  incident_reassigned: 10, user_contact: 10, pending_status: 5,
  work_notes_regular_update: 15, resolution_notes_quality: 15,
  resolution_sla: 10, user_confirmation: 5, reopened_user_connect: 5, kba_education: 5,
};

const RESULTS_TIMEOUT_MS = 20 * 60 * 1000;

// ── Progress step definitions (in order) ──────────────────────────────────
// weight = how much of the overall bar each step occupies (must sum to 100)
const STEPS = [
  { id: 'connect', weight: 10 },
  { id: 'fetch',   weight: 20 },
  { id: 'db',      weight: 15 },
  { id: 'enrich',  weight: 15 },
  { id: 'audit',   weight: 30 },
  { id: 'report',  weight: 10 },
];

// ═══════════════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════════════
(function init() {
  // Default date range — last 30 days
  const fmt   = d => d.toISOString().split('T')[0];
  const today = new Date();
  const past  = new Date(today);
  past.setDate(today.getDate() - 30);
  document.getElementById('end-date').value   = fmt(today);
  document.getElementById('start-date').value = fmt(past);

  // Navigation - horizontal tabs
  document.querySelectorAll('.tab-item').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      showView(el.dataset.view);
    });
  });

  // Metric filter buttons
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterMetrics(btn.dataset.filter);
    });
  });

  // Server health
  checkHealth();
  setInterval(checkHealth, 15000);

  // Unload / refresh handling
  window.addEventListener('beforeunload', handleBeforeUnload);
  window.addEventListener('unload', handleUnload);
  window.addEventListener('pagehide', handleUnload);
  handleReloadCancelIfNeeded();
  resumeJobIfAny();
})();

// ═══════════════════════════════════════════════════════════════════════════
// Theme (light only — toggle hidden, kept for future use)
// ═══════════════════════════════════════════════════════════════════════════
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('auditiq_theme', theme);
}
const _themeBtn = document.getElementById('theme-toggle');
if (_themeBtn) {
  _themeBtn.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme');
    applyTheme(cur === 'dark' ? 'light' : 'dark');
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════════════════════════
const PAGE_META = {
  audit  : {},
  results: {},
  metrics: {},
  tickets: {},
};

function showView(view) {
  activeView = view;
  document.querySelectorAll('.tab-item').forEach(el =>
    el.classList.toggle('active', el.dataset.view === view));
  document.querySelectorAll('.view').forEach(el =>
    el.classList.toggle('active', el.id === `view-${view}`));

  if (view === 'metrics') {
    setTimeout(() => {
      document.querySelectorAll('.mc-bar').forEach(el => {
        el.style.width = el.dataset.width || '0%';
      });
    }, 60);
  }
}

function isAuditRunning() {
  return Boolean(currentJobId) &&
    (currentJobStatus === 'running' || currentJobStatus === 'cancelling');
}

function setJobControls(isRunning) {
  document.getElementById('btn-run').disabled = isRunning;
  document.getElementById('btn-cancel').style.display = isRunning ? 'inline-flex' : 'none';
}

// ═══════════════════════════════════════════════════════════════════════════
// Unload / refresh cancellation
// ═══════════════════════════════════════════════════════════════════════════
function handleBeforeUnload(event) {
  if (!isAuditRunning()) return;
  event.preventDefault();
  event.returnValue = '';
  return '';
}

function handleUnload() {
  if (!isAuditRunning() || !currentJobId) return;
  try {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/cancel-report/${currentJobId}`, false);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.send('{}');
  } catch (e) { console.warn('Unload cancel failed:', e); }
  clearSavedJob();
}

function handleReloadCancelIfNeeded() {
  const nav = performance.getEntriesByType('navigation')[0];
  if (!nav || nav.type !== 'reload') return;
  const saved = loadSavedJob();
  if (!saved?.jobId) return;
  try {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API}/cancel-report/${saved.jobId}`, false);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.send('{}');
  } catch (e) { console.warn('Reload cancel failed:', e); }
  clearSavedJob();
}

// ═══════════════════════════════════════════════════════════════════════════
// Server health
// ═══════════════════════════════════════════════════════════════════════════
async function checkHealth() {
  const el   = document.getElementById('server-status');
  const text = document.getElementById('server-status-text');
  try {
    const r = await fetch(`${API}/health`, { signal: AbortSignal.timeout(4000) });
    if (r.ok) {
      el.className     = 'server-status online';
      text.textContent = 'API connected';
    } else throw new Error();
  } catch {
    el.className     = 'server-status offline';
    text.textContent = 'API offline';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Toasts
// ═══════════════════════════════════════════════════════════════════════════
function toast(msg, type = 'info', duration = 4500) {
  const icons = { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' };
  const stack = document.getElementById('toast-stack');
  const el    = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `
    <span class="toast-icon">${icons[type] || 'ℹ'}</span>
    <span class="toast-body">${escHtml(msg)}</span>
    <button class="toast-close" onclick="this.parentElement.remove()">×</button>
  `;
  stack.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    el.addEventListener('animationend', () => el.remove());
  }, duration);
}

// ═══════════════════════════════════════════════════════════════════════════
// Job persistence (sessionStorage)
// ═══════════════════════════════════════════════════════════════════════════
function saveJob(jobId, params) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId, params }));
}
function clearSavedJob() { sessionStorage.removeItem(STORAGE_KEY); }
function loadSavedJob() {
  try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)); } catch { return null; }
}

// ═══════════════════════════════════════════════════════════════════════════
// Progress Steps Engine
// ═══════════════════════════════════════════════════════════════════════════

// Possible step states: 'idle' | 'active' | 'done' | 'error' | 'skipped'
const _stepState = { connect:'idle', fetch:'idle', db:'idle', enrich:'idle', audit:'idle', report:'idle' };
let _auditHeartbeatTimer = null;
let _auditLastTickAt = 0;
let _auditLastKnownTotal = 0;
let _auditPulsePct = 0;

function _stopAuditHeartbeat() {
  if (_auditHeartbeatTimer) {
    clearInterval(_auditHeartbeatTimer);
    _auditHeartbeatTimer = null;
  }
  _auditLastTickAt = 0;
  _auditLastKnownTotal = 0;
  _auditPulsePct = 0;
}

function _paintStepSubProgress(id, pct, labelText) {
  const bar = document.getElementById(`step-${id}-bar`);
  const lbl = document.getElementById(`step-${id}-label`);
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = labelText || '';
  _recalcOverall();
}

function _startAuditHeartbeat(total = 0) {
  if (total > 0) _auditLastKnownTotal = total;
  _auditLastTickAt = Date.now();
  if (_auditHeartbeatTimer) return;

  _auditPulsePct = 8;
  _auditHeartbeatTimer = setInterval(() => {
    if (_stepState.audit !== 'active' || currentJobStatus !== 'running') {
      _stopAuditHeartbeat();
      return;
    }

    const idleMs = Date.now() - _auditLastTickAt;
    if (idleMs < 1800) return;

    if (_auditLastKnownTotal > 0) {
      _auditPulsePct = Math.min(_auditPulsePct + 2, 28);
      const syntheticCurrent = Math.max(1, Math.round(_auditLastKnownTotal * _auditPulsePct / 100));
      _paintStepSubProgress(
        'audit',
        _auditPulsePct,
        `Auditing tickets… working on incident ${syntheticCurrent} of ${_auditLastKnownTotal}`
      );
    } else {
      setStepDesc('audit', 'Auditing tickets… working on the current incident');
    }
  }, 900);
}

function resetSteps() {
  _stopAuditHeartbeat();
  for (const s of STEPS) {
    _stepState[s.id] = 'idle';
    const row = document.getElementById(`step-${s.id}`);
    if (!row) continue;
    row.className = 'step-row';
    setStepDesc(s.id, _defaultDesc(s.id));
    // reset sub-progress bars
    const bar = document.getElementById(`step-${s.id}-bar`);
    const lbl = document.getElementById(`step-${s.id}-label`);
    if (bar) bar.style.width = '0%';
    if (lbl) lbl.textContent = '';
  }
  setOverallProgress(0);
}

function _defaultDesc(id) {
  const map = {
    connect: 'Establishing connection to ServiceNow…',
    fetch  : 'Retrieving incidents from the selected date range…',
    db     : 'Comparing with cached records and identifying changes…',
    enrich : 'Loading SLA data, audit history and work notes…',
    audit  : 'Running 12 quality checks on each incident…',
    report : 'Building Excel report and computing summary…',
  };
  return map[id] || '';
}

function activateStep(id, desc) {
  _stepState[id] = 'active';
  const row = document.getElementById(`step-${id}`);
  if (!row) return;
  row.className = 'step-row active';
  if (desc) setStepDesc(id, desc);
  _recalcOverall();
}

function completeStep(id, desc) {
  _stepState[id] = 'done';
  if (id === 'audit') _stopAuditHeartbeat();
  const row = document.getElementById(`step-${id}`);
  if (!row) return;
  row.className = 'step-row done';
  if (desc) setStepDesc(id, desc);
  // fill sub-bar to 100 if present
  const bar = document.getElementById(`step-${id}-bar`);
  if (bar) bar.style.width = '100%';
  _recalcOverall();
}

function errorStep(id, desc) {
  _stepState[id] = 'error';
  if (id === 'audit') _stopAuditHeartbeat();
  const row = document.getElementById(`step-${id}`);
  if (!row) return;
  row.className = 'step-row error';
  if (desc) setStepDesc(id, desc);
  _recalcOverall();
}

function setStepDesc(id, text) {
  const el = document.getElementById(`step-${id}-desc`);
  if (el) el.textContent = text;
}

// ── Per-step sub-progress throttle (audit has 1000 tickets — don't thrash DOM) ──
let _lastAuditUpdate = 0;

function setStepSubProgress(id, current, total) {
  if (!total) return;
  const pct = Math.round(current / total * 100);

  // For audit, only update DOM every ~200ms or at start/end to avoid freezing
  if (id === 'audit') {
    const now = Date.now();
    const isEndpoint = (current === 1 || current === total || pct % 10 === 0);
    if (!isEndpoint && now - _lastAuditUpdate < 200) return;
    _lastAuditUpdate = now;
    _auditLastTickAt = now;
    _auditLastKnownTotal = total;
    _auditPulsePct = pct;
  }

  _paintStepSubProgress(id, pct, `${current} / ${total}  (${pct}%)`);
}

function _recalcOverall() {
  let done = 0;
  for (const s of STEPS) {
    const st = _stepState[s.id];
    if (st === 'done') {
      done += s.weight;
    } else if (st === 'active') {
      // partial credit based on sub-bar if available
      const bar = document.getElementById(`step-${s.id}-bar`);
      const partial = bar ? parseFloat(bar.style.width || 0) : 0;
      done += s.weight * (partial / 100) * 0.9; // up to 90% of weight while active
    }
  }
  setOverallProgress(Math.min(Math.round(done), 99)); // cap at 99 until fully done
}

function setOverallProgress(pct) {
  const bar = document.getElementById('progress-overall-bar');
  const lbl = document.getElementById('progress-overall-pct');
  if (bar) bar.style.width = pct + '%';
  if (lbl) lbl.textContent = pct + '%';
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE stream — parse log lines and drive step states
// ═══════════════════════════════════════════════════════════════════════════
function streamLogs(jobId) {
  return new Promise(resolve => {
    const es = new EventSource(`${API}/report-stream/${jobId}`);

    es.onmessage = e => {
      if (e.data === '__DONE__')      { es.close(); resolve('done');      return; }
      if (e.data === '__CANCELLED__') { es.close(); resolve('cancelled'); return; }

      const msg = e.data;
      _parseLogLine(msg);
    };

    es.onerror = () => {
      // Let EventSource reconnect on transient issues; completion still comes
      // through the explicit terminal markers or the result poller.
    };
  });
}

function _parseLogLine(msg) {
  // Every line goes to console for debugging
  console.log('[SSE]', msg);

  // ── 1. Connect ────────────────────────────────────────────────────────
  if (msg.includes('Initialising components')) {
    activateStep('connect', 'Initialising components…');
    return;
  }

  // ── 2. Fetch — triggered by main.py "Fetching incidents from" ─────────
  if (msg.includes('Fetching incidents from') && !msg.match(/\[\d+\/\d+\]/)) {
    completeStep('connect', 'Connected to ServiceNow');
    activateStep('fetch', 'Querying ServiceNow for incident list…');
    return;
  }

  // Orchestrator: checking DB cache (still inside fetch step)
  if (msg.includes('Checking database for incidents')) {
    setStepDesc('fetch', 'Checking local database cache…');
    return;
  }

  // Orchestrator: cache count known
  if (msg.includes('incidents in database cache')) {
    const m = msg.match(/Found (\d+) incidents? in database cache/i);
    if (m) setStepDesc('fetch', `Cache has ${m[1]} incidents — querying ServiceNow…`);
    return;
  }

  // Orchestrator: querying SN
  if (msg.includes('Querying ServiceNow for incident list')) {
    setStepDesc('fetch', 'Querying ServiceNow for incident list…');
    return;
  }

  // Orchestrator: SN count known — the key number to show
  const snCountM = msg.match(/Found (\d+) incidents? in ServiceNow/i);
  if (snCountM) {
    setStepDesc('fetch', `Found ${snCountM[1]} incidents in ServiceNow — comparing with cache…`);
    return;
  }

  // ── 3. DB — fires after comparison loop, BEFORE enrichment ───────────
  // "DB comparison done — total_in_range:N  new:N  modified:N  unchanged:N"
  const dbDoneM = msg.match(/DB comparison done.*?total_in_range:(\d+).*?new:(\d+).*?modified:(\d+).*?unchanged:(\d+)/i);
  if (dbDoneM) {
    completeStep('fetch', `Found ${dbDoneM[1]} incidents in date range`);
    activateStep('db',
      `New: ${dbDoneM[2]}  ·  Modified: ${dbDoneM[3]}  ·  Cached: ${dbDoneM[4]}`
    );
    return;
  }

  // "DB analysis" summary line from orchestrator (just update db desc)
  if (msg.includes('DB analysis')) {
    const nM = msg.match(/New:\s*(\d+)/i);
    const mM = msg.match(/Modified:\s*(\d+)/i);
    const uM = msg.match(/Unchanged:\s*(\d+)/i);
    if (nM) setStepDesc('db', `New: ${nM[1]}  ·  Modified: ${mM?.[1]??'0'}  ·  Cached: ${uM?.[1]??'0'}`);
    return;
  }

  // "Fetching and enriching N incidents from ServiceNow..." — DB done, enrich starting
  if (msg.includes('Fetching and enriching')) {
    const m = msg.match(/Fetching and enriching (\d+)/i);
    completeStep('db', `Cache check done — ${m?.[1] ?? ''} incidents to enrich`);
    activateStep('enrich', 'Fetching SLA data, work notes, audit history per ticket…');
    return;
  }

  // "All cached and unchanged" — no enrichment needed, jump straight to load
  if (msg.includes('already cached and unchanged')) {
    completeStep('db', 'All incidents cached — skipping enrichment');
    completeStep('enrich', 'No enrichment needed (all cached)');
    return;
  }

  // ── 4. Enrich — per-ticket progress from _enrich_incident ─────────────
  // "[n/total] Enriching INCxxxxxxx"
  const enrichM = msg.match(/\[(\d+)\/(\d+)\]\s+Enriching\s+(\S+)/i);
  if (enrichM) {
    const cur   = +enrichM[1];
    const total = +enrichM[2];
    const num   = enrichM[3].replace(/\.*$/, '');
    if (_stepState['enrich'] !== 'active') {
      activateStep('enrich', `Enriching ${num}  (${cur} of ${total})`);
    }
    setStepSubProgress('enrich', cur, total);
    setStepDesc('enrich', `Enriching ${num}  (${cur} of ${total})`);
    return;
  }

  // "All N incident(s) enriched."
  const allEnrichedM = msg.match(/All (\d+) \w+\(s\) enriched/i);
  if (allEnrichedM) {
    completeStep('enrich', `All ${allEnrichedM[1]} incidents enriched`);
    return;
  }

  // ── 5. Bridge between enrich and audit ────────────────────────────────
  // "Orchestration done" from main.py — clean up anything still active
  const orchDoneM = msg.match(/Orchestration done.*?new:(\d+).*?modified:(\d+).*?unchanged:(\d+)/i);
  if (orchDoneM) {
    if (_stepState['enrich'] === 'active') completeStep('enrich', 'Data enrichment complete');
    if (_stepState['db'] === 'active')     completeStep('db', `New: ${orchDoneM[1]}  ·  Modified: ${orchDoneM[2]}  ·  Cached: ${orchDoneM[3]}`);
    return;
  }

  // "Orchestration complete" from orchestrator
  if (msg.includes('Orchestration complete')) {
    if (_stepState['enrich'] === 'active') completeStep('enrich', 'Data enrichment complete');
    if (_stepState['db'] === 'active')     completeStep('db', 'Database sync complete');
    return;
  }

  // ── 6. Load from DB ───────────────────────────────────────────────────
  if (msg.includes('Loading incidents from database')) {
    if (_stepState['db'] === 'active')     completeStep('db', 'Database check complete');
    if (_stepState['enrich'] === 'active') completeStep('enrich', 'Enrichment complete');
    return;
  }

  // "Loaded N incident(s) from database for this range"
  const loadM = msg.match(/Loaded (\d+) incident/i);
  if (loadM) {
    // update db desc if already done, just informational
    const el = document.getElementById('step-db-desc');
    if (el && _stepState['db'] === 'done') el.textContent = `${loadM[1]} incidents loaded from database`;
    return;
  }

  // ── 7. Audit ──────────────────────────────────────────────────────────
  // "Starting audit for N incident(s)..."
  const startAuditM = msg.match(/Starting audit for (\d+)/i);
  if (startAuditM || msg.includes('Initialising Excel')) {
    if (_stepState['db'] === 'active')     completeStep('db', 'Database check complete');
    if (_stepState['enrich'] === 'active') completeStep('enrich', 'Enrichment complete');
    // Always (re)activate audit at this point so spinner shows
    const n = startAuditM?.[1] ?? '';
    activateStep('audit', `Running quality checks${n ? ` on ${n} tickets` : ''}…`);
    _startAuditHeartbeat(n ? Number(n) : 0);
    return;
  }

  // "[n/total] Auditing INCxxxxxxx"
  const auditM = msg.match(/\[(\d+)\/(\d+)\]\s+Auditing\s+(\S+)/i);
  if (auditM) {
    const cur   = +auditM[1];
    const total = +auditM[2];
    const num   = auditM[3].replace(/\.*$/, ''); // strip trailing dots
    if (_stepState['audit'] !== 'active') activateStep('audit', `Auditing ${num}  (${cur} of ${total})`);
    _startAuditHeartbeat(total);
    setStepSubProgress('audit', cur, total);
    // Throttle desc update: only update on every 10th ticket or first/last
    if (cur === 1 || cur === total || cur % 10 === 0) {
      setStepDesc('audit', `Auditing ${num}  (${cur} of ${total})`);
    }
    return;
  }

  // ── 8. Report ─────────────────────────────────────────────────────────
  if (msg.includes('Generating Excel report')) {
    completeStep('audit', 'All tickets audited');
    activateStep('report', 'Generating Excel report and computing summary…');
    setStepDesc('report', 'Writing the workbook to disk…');
    return;
  }

  if (msg.includes('Excel report saved')) {
    setStepDesc('report', 'Workbook saved, building results payload…');
    return;
  }

  if (msg.match(/Audit complete/i)) {
    completeStep('audit', 'All tickets audited');
    activateStep('report', 'Generating Excel report and computing summary…');
    return;
  }

  // ── Error guard ───────────────────────────────────────────────────────
  if (msg.match(/\bFATAL\b/i) || msg.match(/✗.*error/i)) {
    for (const s of STEPS) {
      if (_stepState[s.id] === 'active') {
        errorStep(s.id, msg.replace(/^\[[\d\s\-:]+\]\s*/, '').slice(0, 90));
        break;
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Start audit
// ═══════════════════════════════════════════════════════════════════════════
async function startAudit() {
  const startDate     = document.getElementById('start-date').value;
  const endDate       = document.getElementById('end-date').value;
  const resolverGroup = document.getElementById('resolver-group').value.trim();
  const thresholdRaw  = document.getElementById('pass-threshold').value;
  const threshold     = thresholdRaw === '' ? 70 : Number(thresholdRaw);

  if (!startDate || !endDate)
    { toast('Select both start and end dates.', 'warning'); return; }
  if (startDate > endDate)
    { toast('Start date must be before end date.', 'warning'); return; }
  if (isNaN(threshold) || threshold < 0 || threshold > 100)
    { toast('Threshold must be 0–100.', 'warning'); return; }

  // Show progress card, hide empty state + inline results
  document.getElementById('empty-state').style.display    = 'none';
  document.getElementById('progress-card').style.display  = '';
  const old = document.getElementById('inline-results-card');
  if (old) old.remove();

  resetSteps();
  setJobControls(true);

  // Job pill
  document.getElementById('job-pill').style.display = 'flex';
  document.getElementById('job-pill-text').textContent = 'Starting…';
  currentJobStatus = 'running';

  try {
    const res = await fetch(`${API}/generate-report`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body   : JSON.stringify({ start_date: startDate, end_date: endDate, resolver_group: resolverGroup, threshold }),
    });
    const data = await res.json();

    if (!res.ok || data.error) {
      toast(data.error || 'Failed to start audit', 'error');
      errorStep('connect', data.error || 'Failed to connect');
      resetRunButton();
      return;
    }

    currentJobId = data.job_id;
    currentJobStatus = 'running';
    saveJob(currentJobId, { startDate, endDate, resolverGroup, threshold });
    document.getElementById('job-pill-text').textContent = `Job ${currentJobId}`;
    toast(`Audit started`, 'info');

    // Immediately show connect step as active — first SSE message may be slow
    activateStep('connect', 'Connecting to ServiceNow instance…');

    void streamLogs(currentJobId);
    await fetchAndRenderResults(currentJobId);

  } catch (err) {
    toast(`Request failed: ${err.message}`, 'error');
    errorStep('connect', `Connection error: ${err.message}`);
  } finally {
    if (!isAuditRunning()) resetRunButton();
  }
}

function resetRunButton() {
  setJobControls(false);
}

// ═══════════════════════════════════════════════════════════════════════════
// Cancel audit
// ═══════════════════════════════════════════════════════════════════════════
async function cancelAudit(skipConfirm = false) {
  if (!isAuditRunning()) { toast('No running audit to cancel.', 'info'); return; }
  if (!skipConfirm) {
    if (!window.confirm('Stop the running audit?')) return;
  }

  currentJobStatus = 'cancelling';
  document.getElementById('job-pill-text').textContent = 'Cancelling…';

  // Mark the current active step as cancelling
  for (const s of STEPS) {
    if (_stepState[s.id] === 'active') {
      setStepDesc(s.id, 'Cancelling…');
      break;
    }
  }

  try {
    const res = await fetch(`${API}/cancel-report/${currentJobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      keepalive: true,
    });

    if (res.ok) {
      handleAuditCancelled('Cancellation requested.');
    } else {
      toast('Cancellation request failed.', 'error');
    }
  } catch (e) { console.warn('Cancel request error:', e); }
}

async function clearAuditFiles() {
  if (!window.confirm('Delete all files inside the audits folder? This cannot be undone.')) return;

  const button = document.getElementById('btn-clear-audits');
  if (button) button.disabled = true;

  try {
    const res = await fetch(`${API}/cleanup-audits`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await res.json();

    if (!res.ok) {
      toast(data.error || 'Failed to clear audits folder', 'error');
      return;
    }

    const deletedCount = data.deleted_count ?? 0;
    const skippedCount = (data.skipped_items || []).length;
    const errorCount = (data.errors || []).length;
    toast(
      `Cleared ${deletedCount} file${deletedCount === 1 ? '' : 's'} from audits folder${errorCount ? `, ${errorCount} error(s)` : ''}${skippedCount ? `, skipped ${skippedCount} folder item(s)` : ''}.`,
      errorCount ? 'warning' : 'success'
    );
  } catch (e) {
    console.warn('Clear audits request error:', e);
    toast(`Request failed: ${e.message}`, 'error');
  } finally {
    if (button) button.disabled = false;
  }
}

function handleAuditCancelled(message) {
  // Mark whichever step is currently active; if none is active, mark the
  // latest non-idle step so the UI visibly lands in a cancelled terminal state.
  let cancelledIndex = -1;
  for (let i = 0; i < STEPS.length; i++) {
    const id = STEPS[i].id;
    if (_stepState[id] === 'active') {
      errorStep(id, 'Cancelled');
      cancelledIndex = i;
      break;
    }
  }

  if (cancelledIndex === -1) {
    for (let i = STEPS.length - 1; i >= 0; i--) {
      const id = STEPS[i].id;
      if (_stepState[id] !== 'idle') {
        errorStep(id, 'Cancelled');
        cancelledIndex = i;
        break;
      }
    }
  }

  if (cancelledIndex === -1) {
    errorStep('connect', 'Cancelled');
    cancelledIndex = 0;
  }

  for (let i = cancelledIndex + 1; i < STEPS.length; i++) {
    const id = STEPS[i].id;
    if (_stepState[id] === 'idle') {
      const row = document.getElementById(`step-${id}`);
      if (row) row.className = 'step-row skipped';
    }
  }

  currentJobStatus = 'cancelled';
  clearSavedJob();
  document.getElementById('job-pill').style.display = 'none';
  resetRunButton();
  toast(message || 'Audit cancelled.', 'warning');
}

// ═══════════════════════════════════════════════════════════════════════════
// Fetch & render results (polls until done)
// ═══════════════════════════════════════════════════════════════════════════
async function fetchAndRenderResults(jobId) {
  const deadline = Date.now() + RESULTS_TIMEOUT_MS;
  while (Date.now() < deadline && currentJobStatus !== 'cancelled') {
    try {
      const r    = await fetch(`${API}/report-results/${jobId}`);
      if (currentJobStatus === 'cancelled') return;
      if (r.status === 202) { await sleep(2000); continue; }
      const data = await r.json();
      if (currentJobStatus === 'cancelled') return;

      if (data.status === 'cancelled') {
        handleAuditCancelled(data.error || 'Audit cancelled.');
        return;
      }
      if (data.status === 'completed' && data.results) {
        // Complete the report step and set overall to 100%
        completeStep('report', 'Report generated successfully');
        setOverallProgress(100);
        await sleep(400); // brief pause so user sees 100%

        renderAll(data.results, jobId);
        document.getElementById('job-pill').style.display = 'none';
        currentJobStatus = 'done';
        toast('Audit complete!', 'success');
        return;
      }
      if (data.status === 'error') {
        // Find active step and mark as error
        for (const s of STEPS) {
          if (_stepState[s.id] === 'active') {
            errorStep(s.id, data.error || 'An error occurred');
            break;
          }
        }
        toast(data.error || 'Audit failed', 'error');
        document.getElementById('job-pill').style.display = 'none';
        currentJobStatus = 'error';
        resetRunButton();
        return;
      }
    } catch (e) {
      console.warn('Poll error:', e.message);
    }
    await sleep(3000);
  }
  if (currentJobStatus === 'cancelled') return;
  toast('Timed out waiting for results', 'error');
  resetRunButton();
}

// ═══════════════════════════════════════════════════════════════════════════
// Resume a job that survived a soft navigation (not refresh)
// ═══════════════════════════════════════════════════════════════════════════
async function resumeJobIfAny() {
  const saved = loadSavedJob();
  if (!saved?.jobId) return;

  currentJobId = saved.jobId;

  try {
    const r = await fetch(`${API}/report-status/${saved.jobId}`);
    if (r.status === 404) { clearSavedJob(); return; }
    const data = await r.json();

    if (data.status === 'running' || data.status === 'cancelling') {
      currentJobStatus = data.status;
      document.getElementById('progress-card').style.display = '';
      document.getElementById('empty-state').style.display   = 'none';
      resetSteps();
      // Advance to a mid-point state to show work is in progress
      completeStep('connect', 'Connected');
      activateStep('fetch', 'Resuming — re-connecting to audit stream…');
      setJobControls(true);
      document.getElementById('job-pill').style.display = 'flex';
      document.getElementById('job-pill-text').textContent =
        data.status === 'cancelling' ? `Cancelling…` : `Job ${saved.jobId}`;
      toast(`Reconnected to running job ${saved.jobId}`, 'info');

      void streamLogs(saved.jobId);
      await fetchAndRenderResults(saved.jobId);

    } else if (data.status === 'done') {
      currentJobStatus = 'done';
      await fetchAndRenderResults(saved.jobId);
      clearSavedJob();
    } else if (data.status === 'cancelled') {
      clearSavedJob();
    }
  } catch { clearSavedJob(); }
}

// ═══════════════════════════════════════════════════════════════════════════
// Render everything
// ═══════════════════════════════════════════════════════════════════════════
function renderAll(results, jobId) {
  currentResults = results;
  allTickets     = results.tickets || [];

  renderSummary(results.summary, jobId);
  renderCharts(results.summary, results.metrics_summary);
  renderMetricsGrid(results.metrics_summary);
  renderTicketTable(allTickets);

  // Nav badge
  const badge = document.getElementById('nav-badge');
  badge.textContent = allTickets.length;
  badge.classList.remove('hidden');

  const s = results.summary;
  document.getElementById('tickets-sub').textContent =
    `${s.total} tickets · ${s.passed} passed · ${s.failed} failed`;

  displayResultsInAuditView(results, jobId);
  clearSavedJob();
}

// ── Summary KPIs ──────────────────────────────────────────────────────────
function renderSummary(s, jobId) {
  document.getElementById('s-total') .textContent = s.total;
  document.getElementById('s-passed').textContent = s.passed;
  document.getElementById('s-failed').textContent = s.failed;
  document.getElementById('s-pct')   .textContent = s.pass_pct + '%';
  document.getElementById('s-avg')   .textContent = (s.avg_score_pct || s.avg_pct || 0) + '%';

  document.getElementById('action-bar-range').textContent =
    `${s.date_range?.start || ''} → ${s.date_range?.end || ''}  ·  Group: ${s.resolver_group || 'All'}  ·  Threshold: ${s.threshold}%`;

  document.getElementById('btn-download').href = `${API}/download-report/${jobId}`;
}

// ── Inline results card in the audit view ─────────────────────────────────
function displayResultsInAuditView(results, jobId) {
  const s    = results.summary;
  const orch = results.orchestration || {};

  const existing = document.getElementById('inline-results-card');
  if (existing) existing.remove();

  const card = document.createElement('div');
  card.id        = 'inline-results-card';
  card.className = 'results-card';
  card.innerHTML = `
    <div class="results-header">
      <div>
        <div class="results-title">✓ Audit Complete</div>
        <div class="results-subtitle">${escHtml(s.date_range?.start || '')} → ${escHtml(s.date_range?.end || '')}  ·  Group: ${escHtml(s.resolver_group || 'All')}  ·  Threshold: ${s.threshold}%</div>
      </div>
      <a class="btn btn-primary" href="${API}/download-report/${encodeURIComponent(jobId)}" target="_blank">
        <svg viewBox="0 0 24 24" fill="none"><path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Download Excel
      </a>
    </div>
    <div class="results-kpi-grid">
      <div class="results-kpi"><div class="results-kpi-label">Total Audited</div><div class="results-kpi-value kpi-blue">${s.total}</div></div>
      <div class="results-kpi"><div class="results-kpi-label">Passed</div><div class="results-kpi-value kpi-green">${s.passed}</div></div>
      <div class="results-kpi"><div class="results-kpi-label">Failed</div><div class="results-kpi-value kpi-red">${s.failed}</div></div>
      <div class="results-kpi"><div class="results-kpi-label">Pass Rate</div><div class="results-kpi-value kpi-amber">${s.pass_pct}%</div></div>
      <div class="results-kpi"><div class="results-kpi-label">Avg Score</div><div class="results-kpi-value kpi-purple">${s.avg_score_pct || s.avg_pct || 0}%</div></div>
    </div>
    <div class="results-details">
      <div class="results-detail-section">
        <div class="results-detail-title">Orchestration Stats</div>
        <div class="results-detail-grid">
          <div class="results-detail-item"><span class="results-detail-key">New from API</span><span class="results-detail-value amber">${orch.new ?? '—'}</span></div>
          <div class="results-detail-item"><span class="results-detail-key">From Cache (DB)</span><span class="results-detail-value green">${orch.unchanged ?? '—'}</span></div>
          <div class="results-detail-item"><span class="results-detail-key">Modified</span><span class="results-detail-value">${orch.modified ?? '—'}</span></div>
        </div>
      </div>
      <div class="results-detail-section">
        <div class="results-detail-title">Explore Results</div>
        <div class="results-nav-buttons">
          <button class="btn btn-outline" onclick="showView('results')">
            <svg viewBox="0 0 24 24" fill="none"><path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm9 0V5a2 2 0 00-2-2h-2a2 2 0 00-2 2v14a2 2 0 002 2h2a2 2 0 002-2z" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>
            Dashboard
          </button>
          <button class="btn btn-outline" onclick="showView('metrics')">
            <svg viewBox="0 0 24 24" fill="none"><path d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/><path d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Metric Analysis
          </button>
          <button class="btn btn-outline" onclick="showView('tickets')">
            <svg viewBox="0 0 24 24" fill="none"><path d="M4 6h16M4 10h16M4 14h8M4 18h8" stroke="currentColor" stroke-width="1.75" stroke-linecap="round"/></svg>
            All Tickets
          </button>
        </div>
      </div>
    </div>
  `;

  // Insert after progress card
  const progressCard = document.getElementById('progress-card');
  progressCard.parentNode.insertBefore(card, progressCard.nextSibling);
}

// ── Charts ────────────────────────────────────────────────────────────────
function renderCharts(summary, metricsSummary) {
  const pct    = summary.pass_pct || 0;
  const passed = summary.passed   || 0;
  const failed = summary.failed   || 0;
  drawDonut(passed, failed);
  document.getElementById('donut-pct').textContent = pct + '%';

  const orch = currentResults?.orchestration || {};
  document.getElementById('audit-info').innerHTML = `
    <div class="info-row"><span class="info-key">Total Audited</span>  <span class="info-val blue">${summary.total}</span></div>
    <div class="info-row"><span class="info-key">Pass Threshold</span> <span class="info-val">${summary.threshold}%</span></div>
    <div class="info-row"><span class="info-key">Avg Score</span>      <span class="info-val">${summary.avg_score_pct || summary.avg_pct || 0}%</span></div>
    <div class="info-row"><span class="info-key">New from API</span>   <span class="info-val amber">${orch.new ?? '—'}</span></div>
    <div class="info-row"><span class="info-key">From Cache</span>     <span class="info-val green">${orch.unchanged ?? '—'}</span></div>
    <div class="info-row"><span class="info-key">Modified</span>       <span class="info-val">${orch.modified ?? '—'}</span></div>
  `;

  if (!metricsSummary) return;
  const sorted = Object.entries(metricsSummary)
    .filter(([, m]) => m.applicable > 0)
    .sort(([, a], [, b]) => a.pass_pct - b.pass_pct)
    .slice(0, 6);

  const container = document.getElementById('top-fails');
  container.innerHTML = '';
  for (const [key, m] of sorted) {
    const label  = METRIC_LABELS[key] || key;
    const cls    = m.pass_pct >= 75 ? 'hi' : m.pass_pct >= 50 ? 'mid' : 'lo';
    container.innerHTML += `
      <div class="fail-row">
        <div class="fail-row-head">
          <span class="fail-row-name">${escHtml(label)}</span>
          <span class="fail-row-pct ${cls}">${m.pass_pct}%</span>
        </div>
        <div class="fail-bar-bg">
          <div class="fail-bar ${cls}" style="width:0%" data-w="${m.pass_pct}%"></div>
        </div>
      </div>
    `;
  }
  requestAnimationFrame(() => {
    container.querySelectorAll('.fail-bar').forEach(el => { el.style.width = el.dataset.w; });
  });
}

// ── Donut chart (vanilla canvas) ─────────────────────────────────────────
function drawDonut(passed, failed) {
  const canvas = document.getElementById('donut-chart');
  if (!canvas) return;
  const ctx   = canvas.getContext('2d');
  const cx = 100, cy = 100, r = 76, lw = 18;
  const total = passed + failed || 1;

  ctx.clearRect(0, 0, 200, 200);

  // Track
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = '#e3e8f0';
  ctx.lineWidth   = lw;
  ctx.stroke();

  // Failed
  if (failed > 0) {
    const a = (failed / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + a);
    ctx.strokeStyle = '#ef4444';
    ctx.lineWidth   = lw;
    ctx.lineCap     = 'butt';
    ctx.stroke();
  }
  // Passed
  if (passed > 0) {
    const start = (failed / total) * Math.PI * 2 - Math.PI / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, start, start + (passed / total) * Math.PI * 2);
    ctx.strokeStyle = '#16a34a';
    ctx.lineWidth   = lw;
    ctx.lineCap     = 'butt';
    ctx.stroke();
  }
}

// ── Metrics grid ──────────────────────────────────────────────────────────
function renderMetricsGrid(metricsSummary) {
  if (!metricsSummary) return;
  const container = document.getElementById('metrics-grid');
  container.innerHTML = '';
  const entries = Object.entries(metricsSummary);
  if (!entries.length) {
    container.innerHTML = '<div class="empty-placeholder">No metrics data available.</div>';
    return;
  }
  for (const [key, m] of entries) {
    const label   = METRIC_LABELS[key] || key;
    const pct     = m.pass_pct || 0;
    const barCls  = pct >= 75 ? 'pass' : pct >= 50 ? 'warn' : 'fail';
    const cardCls = pct < 50  ? 'failing' : pct < 75 ? 'neutral' : 'passing';
    const badgeT  = pct >= 75 ? 'pass'   : pct >= 50 ? 'warn'   : 'fail';
    const badgeLb = pct >= 75 ? 'PASSING': pct >= 50 ? 'WATCH'  : 'FAILING';
    const maxPts  = METRIC_MAX[key] || 0;
    const card    = document.createElement('div');
    card.className       = `metric-card ${cardCls}`;
    card.dataset.metric  = key;
    card.dataset.passPct = pct;
    card.innerHTML = `
      <div class="mc-header">
        <div class="mc-name">${escHtml(label)}</div>
        <div class="mc-badge ${badgeT}">${badgeLb}</div>
      </div>
      <div class="mc-bar-wrap">
        <div class="mc-bar-bg">
          <div class="mc-bar ${barCls}" style="width:0%" data-width="${pct}%"></div>
        </div>
      </div>
      <div class="mc-stats">
        <div class="mc-stat"><div class="mc-stat-val v-pass">${m.yes}</div><div class="mc-stat-label">Pass</div></div>
        <div class="mc-stat"><div class="mc-stat-val v-fail">${m.no}</div><div class="mc-stat-label">Fail</div></div>
        <div class="mc-stat"><div class="mc-stat-val v-na">${m.na}</div><div class="mc-stat-label">N/A</div></div>
        <div class="mc-stat"><div class="mc-stat-val">${pct}%</div><div class="mc-stat-label">Pass %</div></div>
      </div>
      <div class="mc-max-pts">Max: ${maxPts} pts</div>
    `;
    container.appendChild(card);
  }
  if (activeView === 'metrics') {
    setTimeout(() => {
      container.querySelectorAll('.mc-bar').forEach(el => { el.style.width = el.dataset.width || '0%'; });
    }, 60);
  }
}

function filterMetrics(filter) {
  document.querySelectorAll('.metric-card').forEach(card => {
    const pct = parseFloat(card.dataset.passPct || 0);
    const show = filter === 'all' ? true : filter === 'fail' ? pct < 75 : pct >= 75;
    card.style.display = show ? '' : 'none';
  });
}

// ── Ticket table ──────────────────────────────────────────────────────────
function renderTicketTable(tickets) {
  const body = document.getElementById('ticket-table-body');
  body.innerHTML = '';
  if (!tickets || tickets.length === 0) {
    body.innerHTML = '<div class="empty-placeholder">No tickets to display.</div>';
    return;
  }
  tickets.forEach(t => {
    if (!matchesFilter(t)) return;
    const wrap     = document.createElement('div');
    wrap.className = 'ticket-row-wrap';
    const prioNum  = (t.priority || '').match(/^(\d)/)?.[1] || '4';
    const resultCls = (t.quality_result || '').toLowerCase();
    const chips = Object.entries(t.metrics || {}).map(([key, val]) => {
      const cls = val === 'Yes' ? 'yes' : val === 'No' ? 'no' : 'na';
      return `<span class="metric-chip ${cls}"><span class="chip-dot"></span>${escHtml(METRIC_LABELS[key] || key)}</span>`;
    }).join('');
    wrap.innerHTML = `
      <div class="ticket-row" onclick="toggleRow(this)">
        <div class="td-num">${escHtml(t.ticket_number || '—')}</div>
        <div class="td-desc" title="${escHtml(t.short_description || '')}">${escHtml(t.short_description || '—')}</div>
        <div class="td-prio p${prioNum}">${escHtml(t.priority || '—')}</div>
        <div class="td-resolved">${escHtml(t.resolved_by || '—')}</div>
        <div class="td-score"><div class="score-bar"><span>${t.score}/${t.out_of}</span></div></div>
        <div class="td-result"><span class="result-badge ${resultCls}">${escHtml(t.quality_result || '—')}</span></div>
        <div class="td-expand"><span class="expand-btn">▾</span></div>
      </div>
      <div class="ticket-detail">
        <div class="detail-inner">
          <div class="detail-title">Metric Breakdown</div>
          <div class="metrics-chips">${chips}</div>
          ${t.observation ? `<div class="detail-obs"><strong>Observation:</strong> ${escHtml(t.observation)}</div>` : ''}
        </div>
      </div>
    `;
    body.appendChild(wrap);
  });
}

function toggleRow(rowEl) {
  rowEl.closest('.ticket-row-wrap').classList.toggle('open');
}

function filterByResult(btn, value) {
  document.querySelectorAll('.filter-pills .pill').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  resultFilter = value;
  renderTicketTable(allTickets);
}

function filterTickets() {
  renderTicketTable(allTickets);
}

function matchesFilter(t) {
  const q = (document.getElementById('ticket-search')?.value || '').toLowerCase();
  if (resultFilter !== 'all' && t.quality_result !== resultFilter) return false;
  if (!q) return true;
  return (
    (t.ticket_number     || '').toLowerCase().includes(q) ||
    (t.short_description || '').toLowerCase().includes(q) ||
    (t.resolved_by       || '').toLowerCase().includes(q) ||
    (t.priority          || '').toLowerCase().includes(q)
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════════════════════
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
