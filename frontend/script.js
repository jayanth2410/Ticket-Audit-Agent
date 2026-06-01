const API = 'http://localhost:5000/api';

let totalTickets = 0;
let processedTickets = 0;
let currentPhase = 'idle'; // 'fetching', 'enriching', 'auditing'
let currentJobId = null;
let currentSummary = null;
let auditStartDate = null;
let auditEndDate = null;

// Set default dates (last 7 days)
const fmt = d => d.toISOString().split('T')[0];
const today = new Date();
const week = new Date(today);
week.setDate(today.getDate() - 7);
document.getElementById('end-date').value = fmt(today);
document.getElementById('start-date').value = fmt(week);

function setStatus(label, cls = '') {
  const chip = document.getElementById('status-chip');
  chip.textContent = label;
  chip.className = 'status-chip ' + cls;
}

function updateProgressCounter() {
  const counter = document.getElementById('progress-counter');
  if (totalTickets > 0) {
    const percentage = Math.round((processedTickets / totalTickets) * 100);
    const phaseLabel = currentPhase === 'fetching' ? 'Fetching & Enriching' :
                       currentPhase === 'auditing' ? 'Auditing' : 'Processing';
    counter.innerHTML = `${phaseLabel}: <strong>${processedTickets}</strong> of <strong>${totalTickets}</strong> tickets (${percentage}%)`;
  }
}

function log(msg, type = '') {
  const t = document.getElementById('log-terminal');
  const el = document.createElement('span');
  el.className = 'log-line ' + type;
  el.textContent = msg;
  t.appendChild(el);
  t.scrollTop = t.scrollHeight;
  
  // Extract ticket count from "Found X incident(s)" message
  const foundMatch = msg.match(/Found (\d+) incident/);
  if (foundMatch) {
    totalTickets = parseInt(foundMatch[1]);
    updateProgressCounter();
  }
  
  // Extract progress from "[X/Y] Fetching" message
  const fetchMatch = msg.match(/\[(\d+)\/(\d+)\] Fetching/);
  if (fetchMatch) {
    currentPhase = 'fetching';
    processedTickets = parseInt(fetchMatch[1]);
    totalTickets = parseInt(fetchMatch[2]);
    updateProgressCounter();
  }
  
  // Extract progress from "[X/Y] Auditing" message
  const auditMatch = msg.match(/\[(\d+)\/(\d+)\] Auditing/);
  if (auditMatch) {
    currentPhase = 'auditing';
    processedTickets = parseInt(auditMatch[1]);
    totalTickets = parseInt(auditMatch[2]);
    updateProgressCounter();
  }
  
  // Detect enriching phase
  if (msg.includes('Enriching incident data')) {
    currentPhase = 'enriching';
    updateProgressCounter();
  }
}

function clearLog() {
  document.getElementById('log-terminal').innerHTML = '';
}

function streamLogs(jobId) {
  return new Promise(resolve => {
    const es = new EventSource(`${API}/stream/${jobId}`);
    es.onmessage = e => {
      if (e.data === '__DONE__') {
        es.close();
        resolve();
        return;
      }
      const msg = e.data;
      const type = msg.startsWith('ERROR') ? 'err'
                 : msg.includes('✓') ? 'ok'
                 : msg.startsWith('Found') || msg.startsWith('Audit complete') ? 'info'
                 : msg.startsWith('Connecting') || msg.startsWith('Fetching') ? 'info'
                 : '';
      log(msg, type);
    };
    es.onerror = () => {
      es.close();
      resolve();
    };
  });
}

async function startAudit() {
  const startDate = document.getElementById('start-date').value;
  const endDate = document.getElementById('end-date').value;
  const resolverGroup = document.getElementById('resolver-group').value.trim();

  if (!startDate || !endDate) {
    alert('Please select both start and end dates.');
    return;
  }

  if (startDate > endDate) {
    alert('Start date must be before end date.');
    return;
  }

  // Store dates for email
  auditStartDate = startDate;
  auditEndDate = endDate;

  clearLog();
  totalTickets = 0;
  processedTickets = 0;
  document.getElementById('progress-counter').innerHTML = '';
  document.getElementById('progress-section').style.display = 'block';
  document.getElementById('empty-state').classList.add('hidden');
  document.getElementById('results-area').classList.add('hidden');
  document.getElementById('btn-run').disabled = true;
  setStatus('Running', 'running');

  log(`Starting audit: ${startDate} → ${endDate}`, 'info');

  try {
    const res = await fetch(`${API}/run-audit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_date: startDate,
        end_date: endDate,
        resolver_group: resolverGroup,
      }),
    });

    const { job_id, error } = await res.json();
    if (error) {
      log(`Error: ${error}`, 'err');
      setStatus('Error', 'error');
      return;
    }

    // Stream logs
    await streamLogs(job_id);

    // Fetch results
    const rRes = await fetch(`${API}/results/${job_id}`);
    const rData = await rRes.json();

    if (rData.status === 'done' && rData.results) {
      renderResults(rData.results, job_id);
      setStatus('Done', 'done');
    } else {
      log('Audit ended with errors.', 'err');
      setStatus('Error', 'error');
    }
  } catch (err) {
    log(`Request failed: ${err.message}`, 'err');
    setStatus('Error', 'error');
  } finally {
    document.getElementById('btn-run').disabled = false;
  }
}

function renderFailureAnalysis(metrics) {
  const metricList = document.getElementById('metric-list');
  metricList.innerHTML = '';

  // Calculate failure data for each metric
  const failureData = [];
  for (const [metricName, metricStats] of Object.entries(metrics)) {
    // Skip user_confirmation metric
    if (metricName === 'user_confirmation') {
      continue;
    }

    const failures = metricStats.total - metricStats.yes;
    const failurePercentage = metricStats.total > 0
      ? Math.round((failures / metricStats.total) * 100)
      : 0;
    const passPercentage = 100 - failurePercentage;

    if (metricStats.total > 0) {  // Only show metrics that have data
      failureData.push({
        name: metricName,
        failures,
        total: metricStats.total,
        failurePercentage,
        passPercentage,
      });
    }
  }

  // Sort by failure percentage (descending)
  failureData.sort((a, b) => b.failurePercentage - a.failurePercentage);

  // Render as list
  failureData.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'metric-row';
    
    let metricLabel = item.name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());
    
    // Add additional info for user_contact
    if (item.name === 'user_contact') {
      metricLabel = 'User Contact (For Additional Information)';
    }
    
    row.innerHTML = `
      <div class="metric-header">
        <div class="metric-name">${metricLabel}</div>
      </div>
      <div class="metric-content">
        <div class="metric-item">
          <span class="metric-label">Failed Tickets</span>
          <span class="metric-value">${item.failures}/${item.total}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">Pass Rate</span>
          <span class="metric-value pass">${item.passPercentage}%</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">Fail Rate</span>
          <span class="metric-value fail">${item.failurePercentage}%</span>
        </div>
      </div>
    `;
    metricList.appendChild(row);
  });
}

function renderResults(data, jobId) {
  const { summary, metrics } = data;

  // Store for email
  currentJobId = jobId;
  currentSummary = summary;

  document.getElementById('s-total').textContent = summary.total;
  document.getElementById('s-passed').textContent = summary.passed;
  document.getElementById('s-failed').textContent = summary.failed;

  // Render failure analysis
  if (metrics) {
    renderFailureAnalysis(metrics);
  }

  document.getElementById('btn-download').href = `${API}/download/${jobId}`;

  document.getElementById('results-area').classList.remove('hidden');
  document.getElementById('empty-state').classList.add('hidden');
}

async function sendEmailReport() {
  if (!currentJobId || !currentSummary) {
    alert('No audit results available. Please run an audit first.');
    return;
  }

  const btn = document.getElementById('btn-email');
  btn.disabled = true;
  btn.textContent = '⏳ Sending...';

  try {
    const res = await fetch(`${API}/send-email`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: currentJobId,
        start_date: auditStartDate,
        end_date: auditEndDate,
        summary: currentSummary,
      }),
    });

    const result = await res.json();

    if (res.ok) {
      alert('✓ Report sent successfully!');
      btn.textContent = '✓ Sent!';
      setTimeout(() => {
        btn.textContent = '✉ Send via Email';
        btn.disabled = false;
      }, 2000);
    } else {
      alert(`Error: ${result.error || 'Failed to send email'}`);
      btn.textContent = '✉ Send via Email';
      btn.disabled = false;
    }
  } catch (err) {
    alert(`Request failed: ${err.message}`);
    btn.textContent = '✉ Send via Email';
    btn.disabled = false;
  }
}
