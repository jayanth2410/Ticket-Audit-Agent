# Ticket Audit System

Production-grade ticket audit solution that fetches incidents from ServiceNow, runs comprehensive audit rules, and generates detailed compliance reports.

**Key Features:**
- 🔄 Smart incident fetching (database-aware - avoids redundant API calls)
- 📊 Comprehensive audit metrics and compliance scoring
- 📈 Real-time log streaming during processing
- 📁 Automatic Excel report generation
- 💾 PostgreSQL database storage
- 🌐 RESTful API with async job processing
- 📧 Email delivery with attachments
- 🎯 Both web UI and CLI support

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Setup & Installation](#setup--installation)
4. [Usage](#usage)
5. [API Endpoints](#api-endpoints)
6. [Data Flow](#data-flow)

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      TICKET AUDIT SYSTEM                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐         ┌──────────────────┐                │
│  │   Frontend   │◄─────►  │   Flask API      │                │
│  │ (Web UI)     │  REST   │   (api.py)       │                │
│  └──────────────┘         └────────┬─────────┘                │
│                                    │                           │
│                          ┌─────────▼──────────┐               │
│                          │  Orchestrator      │               │
│                          │  (intelligent      │               │
│                          │   fetch logic)     │               │
│                          └────┬────────┬──────┘               │
│                               │        │                      │
│         ┌─────────────────────┘        └──────────────────┐  │
│         │                                                  │  │
│    ┌────▼──────┐                                  ┌───────▼──┐
│    │ ServiceNow │                                 │ PostgreSQL│
│    │   API      │                                 │ Database  │
│    └────────────┘                                 └───────────┘
│         │                                                  │
│    ┌────▼─────────┐  Enriched Data  ┌──────────────┐   │
│    │   Enricher   ├───────────────►  │   Auditor    │   │
│    │ (parallel)   │                  │  (rules)     │   │
│    └──────────────┘                  └──────────────┘   │
│                                             │            │
│                                      ┌──────▼──────┐    │
│                                      │   Excel     │    │
│                                      │   Report    │    │
│                                      └─────────────┘    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
Ticket-Audit/
├── backend/
│   ├── api.py                        ← Flask REST API server (WEB ENTRY POINT)
│   ├── main.py                       ← CLI entry point (TERMINAL ONLY)
│   ├── incident_orchestrator.py      ← Smart fetch orchestration
│   ├── incident_fetcher.py           ← ServiceNow API calls
│   ├── incident_storage.py           ← Database storage logic
│   ├── db_config.py                  ← PostgreSQL config
│   ├── db_modal.py                   ← SQLAlchemy ORM models
│   ├── auditor.py                    ← Audit rules & compliance checks
│   ├── excel_handler.py              ← Excel report generation
│   ├── llm.py                        ← AI scoring (optional)
│   ├── check_llm.py                  ← LLM testing utility
│   └── __pycache__/
│
├── frontend/
│   ├── index.html                    ← Web UI (open in browser)
│   ├── script.js                     ← Frontend logic
│   └── styles.css                    ← Styling
│
├── Audit_Report_Template.xlsx        ← Excel template
├── .env                              ← Environment variables
├── requirements.txt                  ← Python dependencies
└── README.md                         ← This file
```

---

## Setup & Installation

### 1. Prerequisites

- Python 3.8+
- PostgreSQL (with database created)
- ServiceNow instance access
- SMTP credentials (optional, for email)

### 2. Clone & Install

```bash
# Navigate to project
cd Ticket-Audit

# Install Python packages
pip install -r requirements.txt
```

### 3. Database Setup

Create a PostgreSQL database:

```sql
CREATE DATABASE TicketAudit;
```

### 4. Environment Configuration

Create `.env` file in project root:

```env
# ServiceNow
SERVICENOW_INSTANCE=https://your-instance.service-now.com
SERVICENOW_USER=your_username
SERVICENOW_PASSWORD=your_password

# PostgreSQL
DATABASE_URL=postgresql://postgres:password@localhost/TicketAudit

# Email (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
SMTP_FROM=your_email@gmail.com

# Optional
GROQ_API_KEY=your_groq_api_key (for LLM features)
FLASK_ENV=development
FLASK_DEBUG=True
```

### 5. Place Excel Template

Copy your `Audit_Report_Template.xlsx` to the project root directory.

---

## Usage

### Option 1: Web UI (Recommended)

```bash
# Start the API server
cd backend
python api.py

# In browser, open:
# http://localhost:5000/frontend/index.html
# OR
# file:///path/to/Ticket-Audit/frontend/index.html
```

**Frontend Flow:**
1. Enter start date and end date
2. (Optional) Enter resolver group filter
3. (Optional) Adjust pass threshold (default: 70%)
4. Click "Start Audit"
5. Watch real-time log stream
6. View results table
7. Download Excel or email report

### Option 2: Terminal/CLI

```bash
cd backend
python main.py
```

The system will:
1. Fetch incidents for hardcoded date range
2. Check database for existing records
3. Only fetch new/modified incidents from ServiceNow
4. Audit each incident
5. Generate Excel report
6. Store all data in PostgreSQL

---

## API Endpoints

### Base URL
```
http://localhost:5000/api
```

### Endpoints

#### 1. Health Check
```http
GET /api/health

Response: 200 OK
{
  "status": "healthy",
  "timestamp": "2026-06-15T10:30:00.000000"
}
```

#### 2. Start Audit Job
```http
POST /api/run-audit
Content-Type: application/json

{
  "start_date": "2026-05-26",
  "end_date": "2026-05-28",
  "resolver_group": "optional_group",
  "pass_threshold": 70
}

Response: 202 Accepted
{
  "job_id": "a1b2c3d4e5f6",
  "status": "queued",
  "timestamp": "2026-06-15T10:30:00.000000"
}
```

#### 3. Stream Live Logs
```http
GET /api/stream/{job_id}

Response: Server-Sent Events (SSE) stream
data: [2026-06-15 10:30:00] Initializing components...
data: [2026-06-15 10:30:01] Starting orchestrated fetch...
data: [2026-06-15 10:30:05] Found 3 incidents in database...
...
data: __DONE__
```

#### 4. Get Job Status
```http
GET /api/status/{job_id}

Response: 200 OK
{
  "job_id": "a1b2c3d4e5f6",
  "status": "running",
  "timestamp": "2026-06-15T10:30:00.000000"
}

Possible statuses: "running", "done", "error"
```

#### 5. Get Final Results
```http
GET /api/results/{job_id}

Response: 200 OK (when done) / 202 Accepted (if still running)
{
  "job_id": "a1b2c3d4e5f6",
  "status": "completed",
  "results": {
    "tickets": [
      {
        "ticket_number": "INC0000014",
        "created_by": "John Smith",
        "priority": "1 - Critical",
        "resolved_by": "Jane Doe",
        "score": 65,
        "percentage": 65.0,
        "quality_result": "FAIL",
        "metrics": {
          "response_within_sla": "Yes",
          "short_desc_quality": "No",
          ...
        }
      }
    ],
    "summary": {
      "total": 3,
      "passed": 2,
      "failed": 1,
      "errors": 0,
      "pass_pct": 66.7,
      "avg_pct": 68.3,
      "pass_threshold": 70
    },
    "metrics": {
      "response_within_sla": {
        "yes": 2,
        "no": 1,
        "na": 0,
        "applicable": 3,
        "pass_pct": 66.7,
        "fail_pct": 33.3
      }
    },
    "orchestration": {
      "new_incidents": 3,
      "modified_incidents": 0,
      "unchanged_incidents": 0,
      "fetched_from_api": 3
    }
  },
  "timestamp": "2026-06-15T10:30:45.000000"
}
```

#### 6. Download Excel Report
```http
GET /api/download/{job_id}

Response: 200 OK
[Binary Excel File]
```

#### 7. Send Email
```http
POST /api/send-email
Content-Type: application/json

{
  "job_id": "a1b2c3d4e5f6",
  "email": "recipient@company.com"
}

Response: 200 OK
{
  "status": "sent",
  "email": "recipient@company.com",
  "timestamp": "2026-06-15T10:30:50.000000"
}
```

---

## Data Flow

### Complete Audit Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER SUBMITS AUDIT FORM                      │
│              (start_date, end_date, pass_threshold)             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
                   ┌───────────────────┐
                   │ POST /api/run-audit│
                   └─────────┬─────────┘
                             │
                   ┌─────────▼──────────┐
                   │ Create Job (in-mem)│
                   │ Start Background   │
                   │ Thread             │
                   └─────────┬──────────┘
                             │
            ┌────────────────▼─────────────────┐
            │  BACKGROUND PROCESSING STARTS    │
            └────────────────┬─────────────────┘
                             │
        ┌────────────────────▼────────────────────┐
        │ STEP 1: Initialize Components          │
        │  - IncidentFetcher                     │
        │  - DBConfig (PostgreSQL)               │
        │  - IncidentOrchestrator                │
        │  [LOG] "Initializing..."               │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────▼─────────────────────┐
        │ STEP 2: Orchestrated Fetch              │
        │  a) Query DB for incidents in date range│
        │  b) Fetch lightweight list from API     │
        │  c) Compare: NEW / MODIFIED / UNCHANGED│
        │  d) Fetch only NEW/MODIFIED from API   │
        │  [LOG] "Found 3 new, 0 modified..."    │
        └────────────────────┬─────────────────────┘
                             │
        ┌────────────────────▼──────────────────────┐
        │ STEP 3: Enrich Incidents (Parallel)      │
        │  For each incident:                      │
        │    - Fetch work notes                    │
        │    - Fetch audit history                │
        │    - Fetch SLA data                     │
        │  [LOG] "[1/3] Enriched INC0000014..."   │
        └────────────────────┬──────────────────────┘
                             │
        ┌────────────────────▼──────────────────────┐
        │ STEP 4: Store in Database                │
        │  - Create/update Incident records        │
        │  - Create AuditHistory records           │
        │  [LOG] "✓ Stored 3 incidents..."        │
        └────────────────────┬──────────────────────┘
                             │
        ┌────────────────────▼───────────────────────┐
        │ STEP 5: Get All Incidents from DB         │
        │  Query incidents with closed_at in range  │
        │  [LOG] "Retrieved 3 incidents from DB..." │
        └────────────────────┬───────────────────────┘
                             │
        ┌────────────────────▼───────────────────────────┐
        │ STEP 6: Initialize Excel Report              │
        │  Load template, prepare workbook             │
        │  [LOG] "Initializing Excel report..."        │
        └────────────────────┬───────────────────────────┘
                             │
        ┌────────────────────▼───────────────────────────┐
        │ STEP 7: Audit Each Incident (Loop)           │
        │  For each incident in database:              │
        │    a) Convert ORM to dict                   │
        │    b) Run auditor.get_audit_data()          │
        │    c) Compute score metrics                 │
        │    d) Write row to Excel                    │
        │  [LOG] "[1/3] Auditing INC0000014..."       │
        │  [LOG] "  ✓ INC0000014 → 65% (FAIL)"        │
        │  [LOG] "[2/3] Auditing INC0000005..."       │
        │  [LOG] "  ✓ INC0000005 → 75% (PASS)"        │
        │  [LOG] "[3/3] Auditing INC0000020..."       │
        │  [LOG] "  ✓ INC0000020 → 72% (PASS)"        │
        └────────────────────┬───────────────────────────┘
                             │
        ┌────────────────────▼────────────────────────┐
        │ STEP 8: Finalize & Compute Summary         │
        │  Save Excel file                           │
        │  Calculate aggregate metrics               │
        │  Build metrics summary                     │
        │  [LOG] "Finalizing Excel report..."        │
        │  [LOG] "Audit complete: 2 PASS, 1 FAIL"   │
        └────────────────────┬────────────────────────┘
                             │
        ┌────────────────────▼──────────────────────────┐
        │ STEP 9: Store Results in Job Memory          │
        │  job["results"] = {                          │
        │    "tickets": [...],                         │
        │    "summary": {...},                         │
        │    "metrics": {...}                          │
        │  }                                           │
        │  job["status"] = "done"                      │
        │  [LOG] "__DONE__"                            │
        └────────────────────┬──────────────────────────┘
                             │
            ┌────────────────▼──────────────────┐
            │ BACKGROUND THREAD COMPLETES       │
            └────────────────┬──────────────────┘
                             │
        ┌────────────────────▼──────────────────────┐
        │ Frontend Receives "__DONE__" in SSE       │
        │ Frontend Polls GET /api/results/{job_id}  │
        │ Frontend Displays Results                 │
        │  - Summary: 3 total, 2 passed, 1 failed  │
        │  - Metrics table                         │
        │  - Individual ticket breakdown           │
        │  - Download/Email buttons                │
        └──────────────────────────────────────────┘
```

### Database-Aware Optimization

```
SCENARIO 1: First Audit (2026-05-26 to 2026-05-28)
───────────────────────────────────────────────────
✅ DB Query: "No incidents in DB for this range"
✅ Fetch from API: 3 incidents
✅ Enrich: All 3 incidents
✅ Store: All 3 in database
API Calls: ~12 (3 incidents × 4 calls each)

SCENARIO 2: Same Date Range Again (2026-05-26 to 2026-05-28)
────────────────────────────────────────────────────────────
✅ DB Query: "Found 3 incidents in DB"
✅ Fetch from API: 0 incidents (all exist & unchanged)
✅ Enrich: SKIPPED
✅ Store: SKIPPED
API Calls: 1 (lightweight list only)
⏱️ Time: ~5 seconds vs ~60 seconds

SCENARIO 3: Overlapping Range (2026-05-27 to 2026-05-29)
──────────────────────────────────────────────────────
✅ DB Query: "Found 2 incidents (May 27-28)"
✅ Fetch from API: 1 new incident (May 29)
✅ Enrich: Only 1 new incident
✅ Store: 1 new + update existing
API Calls: ~5 (1 new incident × 4 calls + list call)
```

---

## Key Features Explained

### 1. Smart Incident Fetching (Orchestrator)

The `incident_orchestrator.py` intelligently:
- Checks database for existing incidents in date range
- Fetches lightweight list from ServiceNow (sys_id, updated_on)
- Compares to identify NEW, MODIFIED, UNCHANGED
- Only fetches NEW/MODIFIED from API
- Dramatically reduces API calls on repeated audits

### 2. Async Job Processing

- Audit jobs run in background threads
- Frontend never blocks
- User gets immediate job_id
- Can disconnect and reconnect anytime
- Results cached in memory for 1 hour

### 3. Real-Time Log Streaming

- Uses Server-Sent Events (SSE)
- User sees every step in real-time
- Progressive feedback during long audits
- Heartbeat prevents connection timeout

### 4. Comprehensive Audit Metrics

Each ticket is scored on:
- Response SLA compliance
- Description quality
- Priority reassessment
- Incident reassignment
- User contact attempts
- Pending status management
- Regular work notes
- Resolution notes quality
- Resolution SLA compliance
- User confirmation
- Reopened incident handling
- KBA education

### 5. PostgreSQL Database

All data persists:
- Incident records with 130+ fields
- Audit history (field changes)
- Timestamps for tracking
- Easy querying for future audits

---

## Troubleshooting

### Issue: "DATABASE_URL not found"
**Solution:** Ensure `.env` has `DATABASE_URL` set correctly
```env
DATABASE_URL=postgresql://user:password@localhost:5432/TicketAudit
```

### Issue: "ServiceNow authentication failed"
**Solution:** Verify credentials in `.env`
```env
SERVICENOW_INSTANCE=https://your-instance.service-now.com
SERVICENOW_USER=your_username
SERVICENOW_PASSWORD=your_password
```

### Issue: "Excel template not found"
**Solution:** Ensure `Audit_Report_Template.xlsx` is in project root

### Issue: "Port 5000 already in use"
**Solution:** Change port in `api.py`:
```python
app.run(debug=True, port=5001, threaded=True)
```

---

## Development Notes

- **Language:** Python 3.8+
- **Web Framework:** Flask + CORS
- **Database:** PostgreSQL + SQLAlchemy ORM
- **API Style:** RESTful JSON
- **Async:** Threading (background jobs)
- **Real-time:** Server-Sent Events (SSE)

## License

Proprietary - Ticket Audit System

## Support

For issues or feature requests, contact the development team.
3. Click **Run Audit**
4. Watch live progress in the log panel
5. View summary cards, metric pass rates, and per-ticket results
6. Click **Download Excel** to get the full report
