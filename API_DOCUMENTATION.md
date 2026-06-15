# Ticket Audit Agent — API Documentation

**Base URL:** `http://localhost:5000`  
**Content-Type:** `application/json`  
**Auth:** None (internal service)

---

## Overview

The Ticket Audit Agent audits ServiceNow incidents against 12 quality metrics using rule-based checks and LLM analysis. Since auditing can take time (LLM calls per ticket), the API follows an **async job pattern**:

```
POST /api/generate-report   →   job_id
        ↓ poll
GET  /api/report-status/<job_id>   →   running | done | error
        ↓ when done
GET  /api/report-results/<job_id>  →   full audit JSON
        ↓ optional
GET  /api/download-report/<job_id> →   Excel file
```

---

## Endpoints

### 1. Health Check

```
GET /api/health
```

Confirms the server is running.

**Response `200`**
```json
{
  "status": "healthy",
  "timestamp": "2026-06-15T10:00:00.000000"
}
```

---

### 2. Generate Report

```
POST /api/generate-report
```

Starts an audit job in the background. Returns a `job_id` immediately for polling.

#### Request Body

| Field            | Type   | Required | Default | Description |
|------------------|--------|----------|---------|-------------|
| `start_date`     | string | Yes      | —       | Audit range start. Format: `YYYY-MM-DD` |
| `end_date`       | string | Yes      | —       | Audit range end. Format: `YYYY-MM-DD` |
| `resolver_group` | string | No       | `""`    | Filter by ServiceNow `u_tcs_resolver_group`. Empty string = all groups |
| `threshold`      | number | No       | `70`    | PASS/FAIL cutoff as a percentage (0–100) |

**Example Request**
```json
{
  "start_date": "2026-04-01",
  "end_date": "2026-05-31",
  "resolver_group": "TCS-INFRA-SUPPORT",
  "threshold": 70
}
```

**Response `202` — Job started**
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "running",
  "message": "Audit started for 2026-04-01 → 2026-05-31. Poll GET /api/report-status/a1b2c3d4e5f6 to check progress. Fetch results with GET /api/report-results/a1b2c3d4e5f6 when done."
}
```

**Response `400` — Validation error**
```json
{
  "error": "start_date and end_date are required (YYYY-MM-DD)"
}
```

```json
{
  "error": "Invalid date format. Use YYYY-MM-DD"
}
```

```json
{
  "error": "threshold must be between 0 and 100"
}
```

---

### 3. Report Status

```
GET /api/report-status/<job_id>
```

Poll this until `status` is `done` or `error`.

#### Path Parameter

| Parameter | Description |
|-----------|-------------|
| `job_id`  | The job ID returned by `POST /api/generate-report` |

**Response `200`**
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "running",
  "params": {
    "start_date": "2026-04-01",
    "end_date": "2026-05-31",
    "resolver_group": "TCS-INFRA-SUPPORT",
    "threshold": 70.0
  },
  "timestamp": "2026-06-15T10:01:00.000000"
}
```

`status` values:

| Value     | Meaning |
|-----------|---------|
| `running` | Audit is in progress |
| `done`    | Audit complete — fetch results |
| `error`   | Audit failed — check `/api/report-results/<job_id>` for error detail |

**Response `404`**
```json
{
  "error": "Job a1b2c3d4e5f6 not found"
}
```

---

### 4. Report Results

```
GET /api/report-results/<job_id>
```

Returns the full audit results. Call this only after status is `done`.

#### Path Parameter

| Parameter | Description |
|-----------|-------------|
| `job_id`  | The job ID returned by `POST /api/generate-report` |

**Response `202` — Still running**
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "running",
  "message": "Audit still in progress. Try again shortly."
}
```

**Response `200` — Completed**
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "timestamp": "2026-06-15T10:05:00.000000",
  "results": {
    "status": "completed",
    "tickets": [...],
    "summary": {...},
    "metrics_summary": {...},
    "orchestration": {...}
  }
}
```

**Response `500` — Job errored**
```json
{
  "job_id": "a1b2c3d4e5f6",
  "status": "error",
  "error": "Database connection failed"
}
```

---

#### Results Object — Full Schema

##### `tickets` array

Each element represents one audited incident.

```json
{
  "ticket_number"    : "INC0010047",
  "created_by"       : "System Administrator",
  "priority"         : "3 - Moderate",
  "resolver_group"   : "TCS-INFRA-SUPPORT",
  "resolved_by"      : "John Doe",
  "short_description": "Network connectivity issue - Multiple floors affected",
  "metrics": {
    "response_within_sla"      : "Yes",
    "short_desc_quality"       : "Yes",
    "priority_reassessed"      : "NA",
    "incident_reassigned"      : "NA",
    "user_contact"             : "Yes",
    "pending_status"           : "NA",
    "work_notes_regular_update": "Yes",
    "resolution_notes_quality" : "Yes",
    "resolution_sla"           : "Yes",
    "user_confirmation"        : "Yes",
    "reopened_user_connect"    : "NA",
    "kba_education"            : "Yes"
  },
  "score"         : 75,
  "out_of"        : 80,
  "percentage"    : 93.8,
  "quality_result": "PASS",
  "observation"   : "All applicable metrics passed."
}
```

**Ticket fields:**

| Field               | Type   | Description |
|---------------------|--------|-------------|
| `ticket_number`     | string | ServiceNow incident number |
| `created_by`        | string | Who opened the ticket (`opened_by`) |
| `priority`          | string | Incident priority e.g. `1 - Critical`, `3 - Moderate` |
| `resolver_group`    | string | TCS resolver group assigned |
| `resolved_by`       | string | Who resolved the ticket |
| `short_description` | string | Incident short description |
| `metrics`           | object | 12 metric results — each value is `"Yes"`, `"No"`, or `"NA"` |
| `score`             | number | Points earned (NA metrics excluded) |
| `out_of`            | number | Maximum applicable points (NA metrics excluded) |
| `percentage`        | number | `score / out_of * 100`, rounded to 1 decimal |
| `quality_result`    | string | `"PASS"` if percentage ≥ threshold, else `"FAIL"` (or `"ERROR"` if audit crashed) |
| `observation`       | string | Human-readable list of all failed metrics |

**Metric values:**

| Value | Meaning |
|-------|---------|
| `Yes` | Metric passed |
| `No`  | Metric failed — counts against score |
| `NA`  | Not applicable — excluded from scoring |

---

##### `metrics` — 12 Audit Metrics

| Key | Max Score | Source | Description |
|-----|-----------|--------|-------------|
| `response_within_sla` | 5 | SLA table | Was the response SLA not breached? |
| `short_desc_quality` | 5 | LLM | Is the short description meaningful (not just "Issue" or "Error")? |
| `priority_reassessed` | 10 | Audit history | Was priority/impact/urgency changed during the ticket lifecycle? |
| `incident_reassigned` | 10 | Reassignment count + work notes | Was reassignment documented in work notes? |
| `user_contact` | 10 | LLM (work notes) | Did the associate contact the user at any point? |
| `pending_status` | 5 | State history + hold reason | Was the Pending/On-Hold status used correctly with documented reason? |
| `work_notes_regular_update` | 15 | Work note timestamps | Were work notes updated regularly (avg gap ≤ 24 hours)? |
| `resolution_notes_quality` | 15 | LLM (close notes + work notes) | Were resolution steps and findings documented? |
| `resolution_sla` | 10 | SLA table | Was the resolution SLA not breached? |
| `user_confirmation` | 5 | LLM (work notes) | Was user confirmation obtained before closing? |
| `reopened_user_connect` | 5 | LLM (work notes + reopen info) | If ticket was reopened, did associate reconnect with user? |
| `kba_education` | 5 | Work notes keywords + knowledge flag | Was a KBA/knowledge article shared with the user? |

---

##### `summary` object

```json
{
  "total"         : 10,
  "passed"        : 8,
  "failed"        : 2,
  "errors"        : 0,
  "pass_pct"      : 80.0,
  "avg_score_pct" : 87.5,
  "threshold"     : 70.0,
  "date_range"    : {
    "start": "2026-04-01",
    "end"  : "2026-05-31"
  },
  "resolver_group": "TCS-INFRA-SUPPORT"
}
```

| Field           | Type   | Description |
|-----------------|--------|-------------|
| `total`         | number | Total incidents audited |
| `passed`        | number | Incidents that scored ≥ threshold |
| `failed`        | number | Incidents that scored < threshold |
| `errors`        | number | Incidents that failed to audit (exception) |
| `pass_pct`      | number | `passed / total * 100` |
| `avg_score_pct` | number | Average percentage score across all non-error tickets |
| `threshold`     | number | The PASS/FAIL cutoff used for this run |
| `date_range`    | object | The start/end dates passed in the request |
| `resolver_group`| string | The resolver group filter used (`"All"` if none) |

---

##### `metrics_summary` object

Aggregated pass/fail stats per metric across all tickets.

```json
{
  "response_within_sla": {
    "yes"       : 8,
    "no"        : 1,
    "na"        : 1,
    "applicable": 9,
    "pass_pct"  : 88.9,
    "max_score" : 5
  },
  "short_desc_quality": {
    "yes"       : 9,
    "no"        : 1,
    "na"        : 0,
    "applicable": 10,
    "pass_pct"  : 90.0,
    "max_score" : 5
  }
}
```

| Field        | Description |
|--------------|-------------|
| `yes`        | Number of tickets where this metric was `Yes` |
| `no`         | Number of tickets where this metric was `No` |
| `na`         | Number of tickets where this metric was `NA` |
| `applicable` | `yes + no` — tickets where this metric was scored |
| `pass_pct`   | `yes / applicable * 100` |
| `max_score`  | Max points this metric is worth per ticket |

---

##### `orchestration` object

Shows how incidents were sourced for this run.

```json
{
  "new"      : 3,
  "modified" : 1,
  "unchanged": 6
}
```

| Field       | Description |
|-------------|-------------|
| `new`       | Incidents fetched fresh from ServiceNow (not in DB yet) |
| `modified`  | Incidents already in DB but updated in ServiceNow since last fetch |
| `unchanged` | Incidents already in DB and not changed — loaded from DB directly |

---

### 5. Live Log Stream

```
GET /api/report-stream/<job_id>
```

Server-Sent Events (SSE) stream of real-time log messages while the audit runs. Useful for building a live progress UI.

#### Path Parameter

| Parameter | Description |
|-----------|-------------|
| `job_id`  | The job ID returned by `POST /api/generate-report` |

**Response** — `text/event-stream`

Each line is a log message prefixed with `data: `.  
The stream ends with `data: __DONE__`.

```
data: [2026-06-15 10:01:00] Initialising components...
data: [2026-06-15 10:01:01] Fetching incidents from 2026-04-01 to 2026-05-31...
data: [2026-06-15 10:01:05] Orchestration done — new:3 modified:1 unchanged:6
data: [2026-06-15 10:01:05] Loading incidents from database...
data: [2026-06-15 10:01:05] Starting audit for 10 incident(s)...
data: [2026-06-15 10:01:06] [1/10] Auditing INC0010047...
data: [2026-06-15 10:01:08]   ✓ INC0010047 → 93.8% PASS
...
data: __DONE__
```

**Browser usage:**
```javascript
const es = new EventSource(`http://localhost:5000/api/report-stream/${jobId}`);
es.onmessage = (e) => {
  if (e.data === "__DONE__") { es.close(); return; }
  console.log(e.data);
};
```

---

### 6. Download Excel Report

```
GET /api/download-report/<job_id>
```

Downloads the generated `.xlsx` audit report file. Only available after status is `done`.

#### Path Parameter

| Parameter | Description |
|-----------|-------------|
| `job_id`  | The job ID returned by `POST /api/generate-report` |

**Response `200`** — Binary file download  
`Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`  
`Content-Disposition: attachment; filename="Audit_Report_<job_id>.xlsx"`

**Response `404`**
```json
{
  "error": "Report file not ready or already deleted"
}
```

> **Note:** Report files are automatically deleted 1 hour after the job completes.

---

## Error Responses

All errors follow a consistent shape:

```json
{
  "error": "Human-readable error message"
}
```

| HTTP Code | When |
|-----------|------|
| `400`     | Invalid request body (missing fields, bad date format, invalid threshold) |
| `202`     | Job still running (on results endpoint) |
| `404`     | Job ID not found, or file already deleted |
| `500`     | Unexpected server error or audit job crashed |

---

## Complete Postman Workflow

### Step 1 — Start audit
```
POST http://localhost:5000/api/generate-report
Content-Type: application/json

{
    "start_date": "2026-04-01",
    "end_date": "2026-05-31",
    "resolver_group": "",
    "threshold": 70
}
```
Copy the `job_id` from the response.

### Step 2 — Poll until done
```
GET http://localhost:5000/api/report-status/<job_id>
```
Repeat until `status` is `done`.

### Step 3 — Get results
```
GET http://localhost:5000/api/report-results/<job_id>
```

### Step 4 — Download Excel (optional)
```
GET http://localhost:5000/api/download-report/<job_id>
```

---

## Notes

- Jobs are kept in memory for **1 hour** after completion, then automatically cleaned up along with the Excel file.
- The audit pipeline is **DB-aware** — it only fetches from ServiceNow what isn't already in the database or has been modified since the last fetch. Subsequent calls for the same date range are much faster.
- LLM calls (Groq / Llama-3.3-70b) are made for `short_desc_quality`, `resolution_notes_quality`, `user_contact`, `user_confirmation`, and `reopened_user_connect`. These are the slowest operations per ticket.
- The server supports multiple concurrent audit jobs.
