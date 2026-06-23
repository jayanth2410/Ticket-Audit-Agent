/* ══════════════════════════════════════════════════════════════════════════
   AuditIQ — Frontend Script
   ══════════════════════════════════════════════════════════════════════════ */

const API         = 'http://172.19.0.34/api';
const STORAGE_KEY = 'auditiq_job_v2';

// ── State ──────────────────────────────────────────────────────────────────
let currentJobId    = null;
let currentResults  = null;
let activeView      = 'audit';
let resultFilter    = 'all';
let allTickets      = [];
let donutChart      = null;

// ── Metric display labels ──────────────────────────────────────────────────
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

  // Theme
  const saved = localStorage.getItem('auditiq_theme') || 'light';
  applyTheme(saved);

  // Navigation
  document.querySelectorAll('.nav-item').forEach(el => {
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

  // Check server health
  checkHealth();
  setInterval(checkHealth, 15000);

  // Resume job if any
  resumeJobIfAny();
})();

// ═══════════════════════════════════════════════════════════════════════════
// Theme
// ═══════════════════════════════════════════════════════════════════════════
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.getElementById('theme-label').textContent =
    theme === 'dark' ? 'Dark Mode' : 'Light Mode';
  localStorage.setItem('auditiq_theme', theme);
}

document.getElementById('theme-toggle').addEventListener('click', () => {
  const current = document.documentElement.getAttribute('data-theme');
  applyTheme(current === 'dark' ? 'light' : 'dark');
});

// ═══════════════════════════════════════════════════════════════════════════
// Navigation
// ═══════════════════════════════════════════════════════════════════════════
const PAGE_META = {
  audit  : { title: 'Run Audit',       sub: 'Configure and launch a new audit' },
  results: { title: 'Results',         sub: 'Summary and distribution overview' },
  metrics: { title: 'Metric Analysis', sub: 'Quality breakdown across 12 audit dimensions' },
  tickets: { title: 'Tickets',         sub: 'Per-ticket audit results' },
};

function showView(view) {
  activeView = view;

  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.view === view);
  });
  document.querySelectorAll('.view').forEach(el => {
    el.classList.toggle('active', el.id === `view-${view}`);
  });

  const meta = PAGE_META[view] || {};
  document.getElementById('page-title').textContent   = meta.title || '';
  document.getElementById('breadcrumb').textContent   = meta.sub   || '';

  // Trigger bar animations when switching to metrics
  if (view === 'metrics') {
    setTimeout(() => {
      document.querySelectorAll('.mc-bar').forEach(el => {
        el.style.width = el.dataset.width || '0%';
      });
    }, 60);
  }
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
      el.className   = 'server-status online';
      text.textContent = 'API connected';
    } else throw new Error();
  } catch {
    el.className   = 'server-status offline';
    text.textContent = 'API offline';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Toasts
// ═══════════════════════════════════════════════════════════════════════════
function toast(msg, type = 'info', duration = 4000) {
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
// Log terminal
// ═══════════════════════════════════════════════════════════════════════════
function log(msg, type = '') {
  const t  = document.getElementById('log-terminal');
  const el = document.createElement('span');
  el.className   = `log-line ${type}`;
  el.textContent = msg;
  t.appendChild(el);
  t.scrollTop = t.scrollHeight;
}

function clearLog() {
  document.getElementById('log-terminal').innerHTML = '';
}

function updateProgress(processed, total, phase) {
  const el = document.getElementById('terminal-progress');
  if (total > 0) {
    const pct = Math.round(processed / total * 100);
    el.textContent = `${phase} ${processed}/${total} (${pct}%)`;
  } else {
    el.textContent = '';
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Job persistence
// ═══════════════════════════════════════════════════════════════════════════
function saveJob(jobId, params) {
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId, params }));
}
function clearSavedJob()  { sessionStorage.removeItem(STORAGE_KEY); }
function loadSavedJob() {
  try { return JSON.parse(sessionStorage.getItem(STORAGE_KEY)); } catch { return null; }
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

  if (!startDate || !endDate)                          { toast('Select both start and end dates.', 'warning');  return; }
  if (startDate > endDate)                             { toast('Start date must be before end date.', 'warning'); return; }
  if (isNaN(threshold) || threshold < 0 || threshold > 100) { toast('Threshold must be 0–100.', 'warning'); return; }

  // Show terminal
  clearLog();
  document.getElementById('empty-state').style.display  = 'none';
  document.getElementById('terminal-card').style.display = '';
  document.getElementById('btn-run').disabled = true;

  // Job pill
  const pill = document.getElementById('job-pill');
  pill.style.display = 'flex';
  document.getElementById('job-pill-text').textContent = 'Starting…';

  log(`Audit request: ${startDate} → ${endDate}  threshold: ${threshold}%`, 'info');

  try {
    const res = await fetch(`${API}/generate-report`, {
      method : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body   : JSON.stringify({
        start_date    : startDate,
        end_date      : endDate,
        resolver_group: resolverGroup,
        threshold,
      }),
    });

    const data = await res.json();
    if (!res.ok || data.error) {
      log(`Error: ${data.error || 'Failed to start audit'}`, 'err');
      toast(data.error || 'Failed to start audit', 'error');
      resetRunButton();
      return;
    }

    currentJobId = data.job_id;
    saveJob(currentJobId, { startDate, endDate, resolverGroup, threshold });
    document.getElementById('job-pill-text').textContent = `Job ${currentJobId}`;
    toast(`Audit started — job ${currentJobId}`, 'success');

    await streamLogs(currentJobId);
    await fetchAndRenderResults(currentJobId);

  } catch (err) {
    log(`Request failed: ${err.message}`, 'err');
    toast(`Request failed: ${err.message}`, 'error');
  } finally {
    resetRunButton();
  }
}

function resetRunButton() {
  document.getElementById('btn-run').disabled = false;
}

// ═══════════════════════════════════════════════════════════════════════════
// SSE log stream
// ═══════════════════════════════════════════════════════════════════════════
function streamLogs(jobId) {
  return new Promise(resolve => {
    const es = new EventSource(`${API}/report-stream/${jobId}`);

    es.onmessage = e => {
      if (e.data === '__DONE__') { es.close(); resolve(); return; }
      const msg = e.data;

      // Classify line type
      const type = msg.includes('✓') || msg.includes('PASS')           ? 'ok'
                 : msg.includes('✗') || msg.includes('Error') || msg.includes('FATAL') ? 'err'
                 : msg.includes('Auditing') || msg.includes('Fetching') ? 'tick'
                 : msg.includes('Initialising') || msg.includes('Orchestration') || msg.includes('complete') ? 'info'
                 : msg.includes('WARNING') ? 'warn'
                 : '';
      log(msg, type);

      // Progress detection
      const auditM = msg.match(/\[(\d+)\/(\d+)\] Auditing/);
      if (auditM) updateProgress(+auditM[1], +auditM[2], 'Auditing');

      const fetchM = msg.match(/\[(\d+)\/(\d+)\] Fetching/);
      if (fetchM) updateProgress(+fetchM[1], +fetchM[2], 'Fetching');
    };

    es.onerror = () => { es.close(); resolve(); };
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// Fetch & render results
// ═══════════════════════════════════════════════════════════════════════════
async function fetchAndRenderResults(jobId) {
  // Poll until done (stream may have ended before job finished)
  let attempts = 0;
  while (attempts < 60) {
    try {
      const r = await fetch(`${API}/report-results/${jobId}`);
      if (r.status === 202) { await sleep(2000); attempts++; continue; }
      const data = await r.json();

      if (data.status === 'completed' && data.results) {
        renderAll(data.results, jobId);
        document.getElementById('job-pill').style.display = 'none';
        toast('Audit complete!', 'success');
        return;
      }
      if (data.status === 'error') {
        toast(data.error || 'Audit failed', 'error');
        document.getElementById('job-pill').style.display = 'none';
        return;
      }
    } catch (e) {
      log(`Poll error: ${e.message}`, 'err');
    }
    await sleep(3000);
    attempts++;
  }
  toast('Timed out waiting for results', 'error');
}

async function resumeJobIfAny() {
  const saved = loadSavedJob();
  if (!saved?.jobId) return;

  currentJobId = saved.jobId;

  // Check if it still exists on the server
  try {
    const r = await fetch(`${API}/report-status/${saved.jobId}`);
    if (r.status === 404) { clearSavedJob(); return; }

    const data = await r.json();

    if (data.status === 'running') {
      document.getElementById('terminal-card').style.display = '';
      document.getElementById('empty-state').style.display   = 'none';
      document.getElementById('btn-run').disabled = true;
      document.getElementById('job-pill').style.display = 'flex';
      document.getElementById('job-pill-text').textContent = `Job ${saved.jobId}`;
      log(`Resuming job ${saved.jobId}…`, 'info');
      toast(`Reconnected to running job ${saved.jobId}`, 'info');
      await streamLogs(saved.jobId);
      await fetchAndRenderResults(saved.jobId);
      resetRunButton();
    } else if (data.status === 'done') {
      // Already finished — fetch results directly
      await fetchAndRenderResults(saved.jobId);
      clearSavedJob();
    }
  } catch {
    clearSavedJob();
  }
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

  // Update tickets sub-text
  const s = results.summary;
  document.getElementById('tickets-sub').textContent =
    `${s.total} tickets · ${s.passed} passed · ${s.failed} failed`;

  // Navigate to Results view
  showView('results');
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

// ── Charts ────────────────────────────────────────────────────────────────
function renderCharts(summary, metricsSummary) {
  // Donut
  const pct    = summary.pass_pct || 0;
  const passed = summary.passed   || 0;
  const failed = summary.failed   || 0;
  drawDonut(passed, failed);
  document.getElementById('donut-pct').textContent = pct + '%';

  // Audit info
  const orch = currentResults?.orchestration || {};
  document.getElementById('audit-info').innerHTML = `
    <div class="info-row"><span class="info-key">Total Audited</span>    <span class="info-val blue">${summary.total}</span></div>
    <div class="info-row"><span class="info-key">Pass Threshold</span>   <span class="info-val">${summary.threshold}%</span></div>
    <div class="info-row"><span class="info-key">Avg Score</span>        <span class="info-val">${summary.avg_score_pct || summary.avg_pct || 0}%</span></div>
    <div class="info-row"><span class="info-key">New from API</span>     <span class="info-val amber">${orch.new ?? '—'}</span></div>
    <div class="info-row"><span class="info-key">From Cache (DB)</span>  <span class="info-val green">${orch.unchanged ?? '—'}</span></div>
    <div class="info-row"><span class="info-key">Modified</span>         <span class="info-val">${orch.modified ?? '—'}</span></div>
  `;

  // Top failing metrics
  if (!metricsSummary) return;
  const sorted = Object.entries(metricsSummary)
    .filter(([, m]) => m.applicable > 0)
    .sort(([, a], [, b]) => a.pass_pct - b.pass_pct)
    .slice(0, 6);

  const container = document.getElementById('top-fails');
  container.innerHTML = '';
  for (const [key, m] of sorted) {
    const label   = METRIC_LABELS[key] || key;
    const barCls  = m.pass_pct >= 75 ? 'hi' : m.pass_pct >= 50 ? 'mid' : 'lo';
    const pctCls  = m.pass_pct >= 75 ? 'hi' : m.pass_pct >= 50 ? 'mid' : 'lo';
    container.innerHTML += `
      <div class="fail-row">
        <div class="fail-row-head">
          <span class="fail-row-name">${escHtml(label)}</span>
          <span class="fail-row-pct ${pctCls}">${m.pass_pct}%</span>
        </div>
        <div class="fail-bar-bg">
          <div class="fail-bar ${barCls}" style="width:0%" data-w="${m.pass_pct}%"></div>
        </div>
      </div>
    `;
  }
  // Animate bars
  requestAnimationFrame(() => {
    container.querySelectorAll('.fail-bar').forEach(el => {
      el.style.width = el.dataset.w;
    });
  });
}

// ── Donut chart (vanilla canvas) ─────────────────────────────────────────
function drawDonut(passed, failed) {
  const canvas = document.getElementById('donut-chart');
  if (!canvas) return;
  const ctx    = canvas.getContext('2d');
  const cx     = 100; const cy = 100; const r = 76; const lineW = 18;
  const total  = passed + failed || 1;
  const isDark = document.documentElement.getAttribute('data-theme') === 'dark';

  ctx.clearRect(0, 0, 200, 200);

  // Track
  ctx.beginPath();
  ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = isDark ? '#2a2e3d' : '#e4e7ec';
  ctx.lineWidth   = lineW;
  ctx.stroke();

  // Failed segment
  if (failed > 0) {
    const failAngle = (failed / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + failAngle);
    ctx.strokeStyle = '#dc2626';
    ctx.lineWidth   = lineW;
    ctx.lineCap     = 'butt';
    ctx.stroke();
  }

  // Passed segment
  if (passed > 0) {
    const passStart = (failed / total) * Math.PI * 2 - Math.PI / 2;
    const passEnd   = passStart + (passed / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.arc(cx, cy, r, passStart, passEnd);
    ctx.strokeStyle = '#16a34a';
    ctx.lineWidth   = lineW;
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
  if (entries.length === 0) {
    container.innerHTML = '<div class="empty-placeholder">No metrics data available.</div>';
    return;
  }

  for (const [key, m] of entries) {
    const label   = METRIC_LABELS[key] || key;
    const pct     = m.pass_pct || 0;
    const barCls  = pct >= 75 ? 'pass' : pct >= 50 ? 'warn' : 'fail';
    const cardCls = pct < 50  ? 'failing' : pct < 75 ? 'neutral' : 'passing';
    const badgeT  = pct >= 75 ? 'pass'    : pct >= 50 ? 'warn'   : 'fail';
    const badgeLb = pct >= 75 ? 'PASSING' : pct >= 50 ? 'WATCH'  : 'FAILING';
    const maxPts  = METRIC_MAX[key] || 0;

    const card = document.createElement('div');
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
        <div class="mc-stat">
          <div class="mc-stat-val v-pass">${m.yes}</div>
          <div class="mc-stat-label">Pass</div>
        </div>
        <div class="mc-stat">
          <div class="mc-stat-val v-fail">${m.no}</div>
          <div class="mc-stat-label">Fail</div>
        </div>
        <div class="mc-stat">
          <div class="mc-stat-val v-na">${m.na}</div>
          <div class="mc-stat-label">N/A</div>
        </div>
        <div class="mc-stat">
          <div class="mc-stat-val">${pct}%</div>
          <div class="mc-stat-label">Pass %</div>
        </div>
      </div>
      <div class="mc-max-pts">Max: ${maxPts} pts</div>
    `;
    container.appendChild(card);
  }

  // Trigger bar animations when metrics view is shown
  if (activeView === 'metrics') {
    setTimeout(() => {
      container.querySelectorAll('.mc-bar').forEach(el => {
        el.style.width = el.dataset.width || '0%';
      });
    }, 60);
  }
}

function filterMetrics(filter) {
  document.querySelectorAll('.metric-card').forEach(card => {
    const pct = parseFloat(card.dataset.passPct || 0);
    const visible =
      filter === 'all'  ? true :
      filter === 'fail' ? pct < 75 :
      filter === 'pass' ? pct >= 75 : true;
    card.style.display = visible ? '' : 'none';
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

    const wrap = document.createElement('div');
    wrap.className = 'ticket-row-wrap';

    // Priority styling
    const prioNum  = (t.priority || '').match(/^(\d)/)?.[1] || '4';
    const prioCls  = `p${prioNum}`;

    const resultCls = (t.quality_result || '').toLowerCase();
    const pct        = t.percentage || 0;
    const scoreCls   = pct >= 75 ? 'pass' : pct >= 50 ? 'warn' : 'fail';

    // Metric chips for expanded detail
    const chips = Object.entries(t.metrics || {}).map(([key, val]) => {
      const cls = val === 'Yes' ? 'yes' : val === 'No' ? 'no' : 'na';
      return `<span class="metric-chip ${cls}"><span class="chip-dot"></span>${escHtml(METRIC_LABELS[key] || key)}</span>`;
    }).join('');

    wrap.innerHTML = `
      <div class="ticket-row" onclick="toggleRow(this)">
        <div class="td-num">${escHtml(t.ticket_number || '—')}</div>
        <div class="td-desc" title="${escHtml(t.short_description || '')}">${escHtml(t.short_description || '—')}</div>
        <div class="td-prio ${prioCls}">${escHtml(t.priority || '—')}</div>
        <div class="td-resolved">${escHtml(t.resolved_by || '—')}</div>
        <div class="td-score">
          <div class="score-bar">
            <span>${t.score}/${t.out_of}</span>
          </div>
        </div>
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

// ── Filters ───────────────────────────────────────────────────────────────
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
