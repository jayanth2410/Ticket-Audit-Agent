"""
main.py — Ticket Audit Agent  (single entry point)
====================================================
Run:
    cd backend
    python main.py

API:
    POST /api/generate-report
    Body: { "start_date", "end_date", "resolver_group"(opt), "threshold"(opt) }

    GET  /api/health
"""

import os
import sys
import json
import logging
import multiprocessing as mp
import queue
import subprocess
import time
import uuid
import smtplib
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent
ROOT_DIR    = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(ROOT_DIR / ".env")

# ── Local imports ─────────────────────────────────────────────────────────────
from incident_fetcher      import IncidentFetcher
from incident_orchestrator import IncidentOrchestrator
from incident_storage      import IncidentStorage
from db_config             import DBConfig
from auditor               import Auditor
from excel_handler         import ExcelHandler

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SN_INSTANCE  = os.getenv("SERVICENOW_INSTANCE", "")
SN_USER      = os.getenv("SERVICENOW_USER",     "")
SN_PASSWORD  = os.getenv("SERVICENOW_PASSWORD", "")
TEMPLATE_PATH = str(ROOT_DIR / "Audit_Report_Template.xlsx")
AUDITS_DIR = ROOT_DIR / "audits"
DEFAULT_THRESHOLD = 70.0

# ── Metric definitions (for scoring) ─────────────────────────────────────────
METRIC_MAX_SCORES = {
    "response_within_sla"      : 5,
    "short_desc_quality"       : 5,
    "priority_reassessed"      : 10,
    "incident_reassigned"      : 10,
    "user_contact"             : 10,
    "pending_status"           : 5,
    "work_notes_regular_update": 15,
    "resolution_notes_quality" : 15,
    "resolution_sla"           : 10,
    "user_confirmation"        : 5,
    "reopened_user_connect"    : 5,
    "kba_education"            : 5,
}

# ── In-memory job store (for async polling) ───────────────────────────────────
JOBS: dict = {}
JOB_TTL    = 3600  # 1 hour

AUDITS_DIR.mkdir(exist_ok=True)

MP_CTX = mp.get_context("spawn")


class JobCancelled(Exception):
    """Raised when a running audit job is cancelled by the user."""
    pass

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


# =============================================================================
# Helpers
# =============================================================================

def _incident_orm_to_dict(incident) -> dict:
    """
    Convert a SQLAlchemy Incident ORM object into a plain dict that the
    Auditor can consume.  All fields the Auditor needs are mapped here.
    Missing fields caused the NA flood — keep this list complete.
    """
    def _dt(val):
        """datetime → ISO string; already string → as-is; None → ''"""
        if val is None:
            return ""
        if isinstance(val, datetime):
            return val.strftime("%Y-%m-%d %H:%M:%S")
        return str(val)

    def _str(val):
        return str(val) if val is not None else ""

    def _int(val):
        try:
            return int(val or 0)
        except (TypeError, ValueError):
            return 0

    # ── Convert audit_history ORM objects to plain dicts ─────────────────────
    audit_history_dicts = []
    for ah in (incident.audit_history or []):
        if hasattr(ah, "fieldname"):
            # It's still an ORM AuditHistory object
            audit_history_dicts.append({
                "fieldname"      : _str(ah.fieldname),
                "oldvalue"       : _str(ah.oldvalue),
                "newvalue"       : _str(ah.newvalue),
                "sys_created_by" : _str(ah.sys_created_by),
                "sys_created_on" : _dt(ah.sys_created_on),
            })
        elif isinstance(ah, dict):
            audit_history_dicts.append(ah)
        # skip stringified repr objects (legacy db_incidents.json artefacts)

    return {
        # identifiers
        "sys_id"              : _str(incident.sys_id),
        "number"              : _str(incident.number),

        # header fields for the report
        "opened_by"           : _str(incident.opened_by),
        "priority"            : _str(incident.priority),
        "u_tcs_resolver_group": _str(incident.u_tcs_resolver_group),
        "assignment_group"    : _str(incident.assignment_group),
        "resolved_by"         : _str(incident.resolved_by),

        # description
        "short_description"   : _str(incident.short_description),
        "description"         : _str(incident.description),

        # state
        "state"               : _str(incident.state),
        "hold_reason"         : _str(incident.hold_reason),

        # dates
        "opened_at"           : _dt(incident.opened_at),
        "closed_at"           : _dt(incident.closed_at),
        "resolved_at"         : _dt(incident.resolved_at),
        "reopened_time"       : _dt(incident.reopened_time),

        # counts
        "reassignment_count"  : _int(incident.reassignment_count),
        "reopen_count"        : _int(incident.reopen_count),

        # SLA
        "sla_data"            : incident.sla_data or {},

        # resolution
        "close_notes"         : _str(incident.close_notes),
        "close_code"          : _str(incident.close_code),

        # journals (work_notes stored as combined string in DB)
        "work_notes"          : _str(incident.work_notes),
        "comments"            : _str(incident.comments),
        "comments_and_work_notes": _str(incident.comments_and_work_notes),

        # KBA flag
        "knowledge"           : incident.knowledge,

        # audit trail
        "audit_history"       : audit_history_dicts,

        # misc
        "priority"            : _str(incident.priority),
        "urgency"             : _str(incident.urgency),
        "severity"            : _str(incident.severity),
        "impact"              : _str(incident.impact),
        "category"            : _str(incident.category),
        "assigned_to"         : _str(incident.assigned_to),
        "caller_id"           : _str(incident.caller_id),
        "company"             : _str(incident.company),
    }


def _compute_score(audit_data: dict, threshold: float) -> dict:
    """Compute score, out_of, percentage and quality_result for one ticket."""
    score  = 0
    out_of = 0

    for metric, max_pts in METRIC_MAX_SCORES.items():
        value = audit_data.get(metric, "NA")
        if value == "NA":
            continue
        out_of += max_pts
        if value == "Yes":
            score += max_pts

    percentage     = round(score / out_of * 100, 1) if out_of > 0 else 0.0
    quality_result = "PASS" if percentage >= threshold else "FAIL"

    return {
        "score"         : score,
        "out_of"        : out_of,
        "percentage"    : percentage,
        "quality_result": quality_result,
    }


def _build_metrics_summary(tickets: list) -> dict:
    """Aggregate per-metric yes/no/na counts across all audited tickets."""
    yes = {m: 0 for m in METRIC_MAX_SCORES}
    no  = {m: 0 for m in METRIC_MAX_SCORES}
    na  = {m: 0 for m in METRIC_MAX_SCORES}

    for t in tickets:
        for metric, val in t.get("metrics", {}).items():
            if val == "Yes":
                yes[metric] += 1
            elif val == "No":
                no[metric]  += 1
            else:
                na[metric]  += 1

    summary = {}
    for metric in METRIC_MAX_SCORES:
        applicable = yes[metric] + no[metric]
        pass_pct   = round(yes[metric] / applicable * 100, 1) if applicable else 0.0
        summary[metric] = {
            "yes"       : yes[metric],
            "no"        : no[metric],
            "na"        : na[metric],
            "applicable": applicable,
            "pass_pct"  : pass_pct,
            "max_score" : METRIC_MAX_SCORES[metric],
        }

    return summary


def _cleanup_old_jobs():
    now = time.time()
    for jid in list(JOBS.keys()):
        job = JOBS[jid]
        if job["status"] in ("done", "error"):
            if now - job.get("finished_at", now) > JOB_TTL:
                for path in (job.get("excel_path"), job.get("record_path")):
                    if path and os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                JOBS.pop(jid, None)


def _raise_if_cancelled(cancel_check):
    if cancel_check():
        raise JobCancelled("Audit cancelled by user")


def _sync_job_state(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return None

    process = job.get("process")
    if job["status"] in ("done", "error", "cancelled"):
        return job

    if process and not process.is_alive():
        if job.get("cancel_requested"):
            job["status"] = "cancelled"
            job["finished_at"] = job.get("finished_at") or time.time()
            return job

        if process.exitcode == 0:
            record_path = job.get("record_path")
            if record_path and os.path.exists(record_path) and job.get("results") is None:
                try:
                    with open(record_path, "r", encoding="utf-8") as fh:
                        payload = json.load(fh)
                    job["results"] = payload.get("results")
                    job["status"] = payload.get("status", "done")
                except Exception as exc:
                    job["status"] = "error"
                    job["error"] = f"Failed to load audit record: {exc}"
            else:
                job["status"] = "done"
            job["finished_at"] = job.get("finished_at") or time.time()
            return job

        job["status"] = "error"
        job["error"] = job.get("error") or f"Audit process exited with code {process.exitcode}"
        job["finished_at"] = job.get("finished_at") or time.time()

    return job


def _find_active_job_id() -> str | None:
    active_jobs = [
        (jid, job)
        for jid, job in JOBS.items()
        if job.get("status") in ("running", "cancelling") and job.get("process")
    ]
    if not active_jobs:
        return None
    active_jobs.sort(key=lambda item: item[1].get("created_at", 0), reverse=True)
    return active_jobs[0][0]


def _terminate_job(job_id: str):
    job = _sync_job_state(job_id)
    if not job:
        return None

    if job["status"] in ("done", "error", "cancelled"):
        return job

    job["cancel_requested"] = True
    if job["status"] == "running":
        job["status"] = "cancelling"

    process = job.get("process")
    cancel_event = job.get("cancel_event")
    if cancel_event:
        cancel_event.set()

    job["log_queue"].put("Cancellation requested by user...")
    if process and process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive() and os.name == "nt" and process.pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                pass
            process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)

    job["status"] = "cancelled"
    job["error"] = "Audit cancelled by user"
    job["finished_at"] = time.time()
    job["log_queue"].put("__CANCELLED__")
    return job


# =============================================================================
# Core audit runner  (runs in a separate process per job)
# =============================================================================

def _run_audit(job_id: str, start_date: str, end_date: str,
               resolver_group: str, threshold: float,
               log_queue, cancel_event, record_path: str):
    """
    Full audit pipeline:
      1. Fetch + enrich incidents from ServiceNow (DB-aware)
      2. Audit each incident through all metric checks
      3. Write Excel report
      4. Return structured JSON results
    """
    job = {
        "log_queue"  : log_queue,
        "status"     : "running",
        "results"    : None,
        "error"      : None,
        "excel_path" : None,
        "record_path": record_path,
        "params"     : {
            "start_date"    : start_date,
            "end_date"      : end_date,
            "resolver_group": resolver_group,
            "threshold"     : threshold,
        },
    }
    log_q = log_queue
    cancel_check = cancel_event.is_set

    def log(msg: str):
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fmt = f"[{ts}] {msg}"
        logger.info(msg)
        log_q.put(fmt)

    tickets = []

    try:
        _raise_if_cancelled(cancel_check)

        # ── 1. Init ───────────────────────────────────────────────────────────
        log("Initialising components...")
        fetcher      = IncidentFetcher(SN_INSTANCE, SN_USER, SN_PASSWORD, log_callback=log)
        db_config    = DBConfig()
        orchestrator = IncidentOrchestrator(db_config, fetcher)

        _raise_if_cancelled(cancel_check)

        # ── 2. Fetch + store (DB-aware, only new/modified from ServiceNow) ────
        log(f"Fetching incidents from {start_date} to {end_date}...")
        orch_result = orchestrator.fetch_and_store(
            start_date     = start_date,
            end_date       = end_date,
            ticket_type    = "incident",
            resolver_group = resolver_group or None,
            cancel_check   = cancel_check,
        )
        analysis = orch_result["analysis"]
        if orch_result.get("cancelled"):
            raise JobCancelled("Audit cancelled by user")
        log(
            f"Orchestration done — new:{analysis['new_count']} "
            f"modified:{analysis['modified_count']} "
            f"unchanged:{analysis['unchanged_count']}"
        )

        _raise_if_cancelled(cancel_check)

        # ── 3. Load incidents from DB ─────────────────────────────────────────
        log("Loading incidents from database...")
        db_result  = orchestrator.get_incidents_in_database(start_date, end_date)
        db_incidents = db_result["incidents"]   # dict {sys_id: ORM object}
        total        = db_result["count"]
        log(f"Loaded {total} incident(s) from database for this range")

        _raise_if_cancelled(cancel_check)

        if total == 0:
            log("No incidents found for the given filters.")
            job["results"] = _build_empty_result(threshold, analysis)
            job["status"]  = "done"
            log_q.put("__DONE__")
            return

        # ── 4. Excel output ───────────────────────────────────────────────────
        excel_path = str(AUDITS_DIR / f"Audit_Report_{job_id}.xlsx")
        record_path = str(AUDITS_DIR / f"Audit_Record_{job_id}.json")
        log(f"Initialising Excel report → {excel_path}")
        excel = ExcelHandler(TEMPLATE_PATH, excel_path, pass_threshold=threshold)
        job["excel_path"] = excel_path
        job["record_path"] = record_path

        # ── 5. Audit loop ─────────────────────────────────────────────────────
        log(f"Starting audit for {total} incident(s)...")

        for idx, (sys_id, incident_orm) in enumerate(db_incidents.items(), 1):
            _raise_if_cancelled(cancel_check)
            number = incident_orm.number

            try:
                incident_dict = _incident_orm_to_dict(incident_orm)
                # Log every ticket for accurate progress tracking
                log(f"[{idx}/{total}] Auditing {number}...")
                auditor       = Auditor(incident_dict)
                audit_data    = auditor.get_audit_data()
                scores        = _compute_score(audit_data, threshold)

                excel.write_audit_row(audit_data)
                log(f"  ✓ {number} audited")

                tickets.append({
                    "ticket_number" : audit_data.get("ticket_number", number),
                    "created_by"    : audit_data.get("created_by", ""),
                    "priority"      : audit_data.get("priority", ""),
                    "resolver_group": audit_data.get("tcs_resolver_group", ""),
                    "resolved_by"   : audit_data.get("resolved_by", ""),
                    "short_description": incident_dict.get("short_description", ""),
                    "metrics"       : {m: audit_data.get(m, "NA") for m in METRIC_MAX_SCORES},
                    "score"         : scores["score"],
                    "out_of"        : scores["out_of"],
                    "percentage"    : scores["percentage"],
                    "quality_result": scores["quality_result"],
                    "observation"   : _build_observation(audit_data),
                })

            except Exception as e:
                logger.exception(f"Error auditing {number}")
                log(f"  ✗ {number} error: {e}")
                tickets.append({
                    "ticket_number" : number,
                    "error"         : str(e),
                    "metrics"       : {},
                    "score"         : 0,
                    "out_of"        : 0,
                    "percentage"    : 0.0,
                    "quality_result": "ERROR",
                    "observation"   : str(e),
                })

            _raise_if_cancelled(cancel_check)

        # ── 6. Save Excel ─────────────────────────────────────────────────────
        log("Generating Excel report...")
        excel.save()
        log("Excel report saved")

        # ── 7. Build summary ──────────────────────────────────────────────────
        passed     = sum(1 for t in tickets if t["quality_result"] == "PASS")
        failed     = sum(1 for t in tickets if t["quality_result"] == "FAIL")
        errors     = sum(1 for t in tickets if t["quality_result"] == "ERROR")
        valid      = [t for t in tickets if t["quality_result"] != "ERROR"]
        avg_pct    = round(sum(t["percentage"] for t in valid) / len(valid), 1) if valid else 0.0
        pass_pct   = round(passed / total * 100, 1) if total else 0.0

        job["results"] = {
            "status" : "completed",
            "tickets": tickets,
            "summary": {
                "total"         : total,
                "passed"        : passed,
                "failed"        : failed,
                "errors"        : errors,
                "pass_pct"      : pass_pct,
                "avg_score_pct" : avg_pct,
                "threshold"     : threshold,
                "date_range"    : {"start": start_date, "end": end_date},
                "resolver_group": resolver_group or "All",
            },
            "metrics_summary": _build_metrics_summary(tickets),
            "orchestration"  : {
                "new"      : analysis["new_count"],
                "modified" : analysis["modified_count"],
                "unchanged": analysis["unchanged_count"],
            },
        }

        audit_record = {
            "job_id"    : job_id,
            "status"    : "completed",
            "created_at" : datetime.utcnow().isoformat(),
            "finished_at": datetime.utcnow().isoformat(),
            "params"    : job.get("params", {}),
            "results"   : job["results"],
        }
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(audit_record, fh, indent=2, default=str)

        job["status"] = "done"
        log(f"Audit complete — {passed} PASS / {failed} FAIL / {errors} ERROR")

    except JobCancelled as e:
        logger.info("Audit job cancelled: %s", job_id)
        log(str(e))
        job["status"] = "cancelled"
        job["error"] = str(e)
        job["results"] = None

    except Exception as e:
        logger.exception("Fatal error in audit job")
        log(f"FATAL: {e}")
        job["status"]  = "error"
        job["error"]   = str(e)
        job["results"] = None

    finally:
        job["finished_at"] = time.time()
        log_q.put("__CANCELLED__" if job.get("status") == "cancelled" else "__DONE__")


def _build_observation(audit_data: dict) -> str:
    """List the failed (No) metrics as a readable string."""
    LABELS = {
        "response_within_sla"      : "Response SLA not met",
        "short_desc_quality"       : "Short description unclear",
        "priority_reassessed"      : "Priority not re-assessed",
        "incident_reassigned"      : "Reassignment details missing",
        "user_contact"             : "User contact not documented",
        "pending_status"           : "Pending status incorrectly used",
        "work_notes_regular_update": "Work notes not updated regularly",
        "resolution_notes_quality" : "Resolution notes incomplete",
        "resolution_sla"           : "Resolution SLA not met",
        "user_confirmation"        : "User confirmation not taken",
        "reopened_user_connect"    : "No user contact after reopen",
        "kba_education"            : "KBA not shared with user",
    }
    failed = [label for metric, label in LABELS.items() if audit_data.get(metric) == "No"]
    return "; ".join(failed) + "." if failed else "All applicable metrics passed."


def _build_empty_result(threshold: float, analysis: dict) -> dict:
    return {
        "status" : "completed",
        "tickets": [],
        "summary": {
            "total"         : 0,
            "passed"        : 0,
            "failed"        : 0,
            "errors"        : 0,
            "pass_pct"      : 0.0,
            "avg_score_pct" : 0.0,
            "threshold"     : threshold,
        },
        "metrics_summary": {},
        "orchestration"  : {
            "new"      : analysis.get("new_count", 0),
            "modified" : analysis.get("modified_count", 0),
            "unchanged": analysis.get("unchanged_count", 0),
        },
    }


# =============================================================================
# API Routes
# =============================================================================

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({
        "status"   : "healthy",
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/generate-report", methods=["POST"])
def generate_report():
    """
    Start an audit job.

    Request body (JSON):
    {
        "start_date"     : "2026-04-01",        -- required, YYYY-MM-DD
        "end_date"       : "2026-05-31",        -- required, YYYY-MM-DD
        "resolver_group" : "TCS-INFRA-SUPPORT", -- optional
        "threshold"      : 70                   -- optional, default 70
    }

    Response 202:
    {
        "job_id" : "abc123",
        "status" : "running",
        "message": "Audit started. Poll /api/report-status/<job_id> for progress."
    }
    """
    try:
        body = request.get_json(force=True) or {}

        start_date     = (body.get("start_date")     or "").strip()
        end_date       = (body.get("end_date")       or "").strip()
        resolver_group = (body.get("resolver_group") or "").strip()

        # Validate required fields
        if not start_date or not end_date:
            return jsonify({
                "error": "start_date and end_date are required (YYYY-MM-DD)"
            }), 400

        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date,   "%Y-%m-%d")
        except ValueError:
            return jsonify({
                "error": "Invalid date format. Use YYYY-MM-DD"
            }), 400

        try:
            threshold = float(body.get("threshold", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD

        if not (0 <= threshold <= 100):
            return jsonify({
                "error": "threshold must be between 0 and 100"
            }), 400

        _cleanup_old_jobs()

        # Create job
        job_id = str(uuid.uuid4())[:12]
        log_queue    = MP_CTX.Queue()
        cancel_event = MP_CTX.Event()
        excel_path   = str(AUDITS_DIR / f"Audit_Report_{job_id}.xlsx")
        record_path  = str(AUDITS_DIR / f"Audit_Record_{job_id}.json")
        JOBS[job_id] = {
            "log_queue"  : log_queue,
            "status"     : "running",
            "cancel_requested": False,
            "results"    : None,
            "error"      : None,
            "excel_path" : excel_path,
            "record_path": record_path,
            "process"    : None,
            "cancel_event": cancel_event,
            "finished_at": None,
            "created_at" : time.time(),
            "params"     : {
                "start_date"    : start_date,
                "end_date"      : end_date,
                "resolver_group": resolver_group,
                "threshold"     : threshold,
            },
        }

        process = MP_CTX.Process(
            target = _run_audit,
            args   = (
                job_id,
                start_date,
                end_date,
                resolver_group,
                threshold,
                log_queue,
                cancel_event,
                record_path,
            ),
            daemon = True,
        )
        process.start()
        JOBS[job_id]["process"] = process

        logger.info(f"Audit job started: {job_id}")

        return jsonify({
            "job_id" : job_id,
            "status" : "running",
            "message": (
                f"Audit started for {start_date} → {end_date}. "
                f"Poll GET /api/report-status/{job_id} to check progress. "
                f"Fetch results with GET /api/report-results/{job_id} when done."
            ),
        }), 202

    except Exception as e:
        logger.exception("Error in /api/generate-report")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cancel-report/<job_id>", methods=["POST"])
def cancel_report(job_id: str):
    if job_id not in JOBS:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    job = _terminate_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    return jsonify({
        "job_id": job_id,
        "status": "cancelled",
        "message": "Cancellation requested",
    }), 202


@app.route("/api/cancel-report/", methods=["POST"])
def cancel_report_active():
    job_id = _find_active_job_id()
    if not job_id:
        return jsonify({"error": "No running job found"}), 404

    _terminate_job(job_id)
    return jsonify({
        "job_id": job_id,
        "status": "cancelled",
        "message": "Cancellation requested",
    }), 202


@app.route("/api/report-status/<job_id>", methods=["GET"])
def report_status(job_id: str):
    """
    Poll job status.

    Response:
    {
        "job_id" : "abc123",
        "status" : "running" | "done" | "error"
    }
    """
    if job_id not in JOBS:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    job = _sync_job_state(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    status = job["status"]
    if status == "running" and job.get("cancel_requested"):
        status = "cancelling"
    return jsonify({
        "job_id"   : job_id,
        "status"   : status,
        "params"   : job.get("params", {}),
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/report-results/<job_id>", methods=["GET"])
def report_results(job_id: str):
    """
    Get the full audit results once the job is done.

    Response shape:
    {
        "job_id": "abc123",
        "status": "completed",
        "results": {
            "tickets": [
                {
                    "ticket_number"   : "INC0010047",
                    "created_by"      : "System Administrator",
                    "priority"        : "3 - Moderate",
                    "resolver_group"  : "TCS-INFRA",
                    "resolved_by"     : "John Doe",
                    "short_description": "...",
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
            ],
            "summary": {
                "total"         : 10,
                "passed"        : 8,
                "failed"        : 2,
                "errors"        : 0,
                "pass_pct"      : 80.0,
                "avg_score_pct" : 87.5,
                "threshold"     : 70.0,
                "date_range"    : {"start": "2026-04-01", "end": "2026-05-31"},
                "resolver_group": "TCS-INFRA"
            },
            "metrics_summary": {
                "response_within_sla": {
                    "yes": 8, "no": 1, "na": 1,
                    "applicable": 9, "pass_pct": 88.9, "max_score": 5
                },
                ...
            },
            "orchestration": { "new": 3, "modified": 1, "unchanged": 6 }
        }
    }
    """
    if job_id not in JOBS:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    job = _sync_job_state(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    if job["status"] == "cancelled":
        return jsonify({
            "job_id": job_id,
            "status": "cancelled",
            "error" : job.get("error", "Audit cancelled by user"),
        }), 200

    if job["status"] == "running":
        return jsonify({
            "job_id" : job_id,
            "status" : "running",
            "message": "Audit still in progress. Try again shortly.",
        }), 202

    if job["status"] == "cancelling":
        return jsonify({
            "job_id" : job_id,
            "status" : "cancelling",
            "message": "Cancellation in progress. Try again shortly.",
        }), 202

    if job["status"] == "error":
        return jsonify({
            "job_id": job_id,
            "status": "error",
            "error" : job.get("error"),
        }), 500

    if job["results"] is None:
        record_path = job.get("record_path")
        if record_path and os.path.exists(record_path):
            try:
                with open(record_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                job["results"] = payload.get("results")
            except Exception as exc:
                job["status"] = "error"
                job["error"] = f"Failed to load audit record: {exc}"
                return jsonify({
                    "job_id": job_id,
                    "status": "error",
                    "error" : job["error"],
                }), 500

    return jsonify({
        "job_id"   : job_id,
        "status"   : "completed",
        "results"  : job["results"],
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/report-stream/<job_id>", methods=["GET"])
def report_stream(job_id: str):
    """
    Server-Sent Events stream — real-time log lines while audit runs.
    Connect with EventSource in the browser.
    """
    if job_id not in JOBS:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    def event_stream():
        log_q = JOBS[job_id]["log_queue"]
        try:
            while True:
                try:
                    msg = log_q.get(timeout=20)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue

                if msg == "__DONE__":
                    yield "data: __DONE__\n\n"
                    break

                if msg == "__CANCELLED__":
                    yield "data: __CANCELLED__\n\n"
                    break

                safe = msg.replace("\n", " ").replace("\r", " ")
                yield f"data: {safe}\n\n"

        except GeneratorExit:
            pass

    return Response(
        event_stream(),
        mimetype = "text/event-stream",
        headers  = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/download-report/<job_id>", methods=["GET"])
def download_report(job_id: str):
    """Download the generated Excel report."""
    if job_id not in JOBS:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    job = _sync_job_state(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    path = job.get("excel_path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "Report file not ready or already deleted"}), 404

    return send_file(
        path,
        as_attachment  = True,
        download_name  = f"Audit_Report_{job_id}.xlsx",
        mimetype       = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/cleanup-audits", methods=["POST"])
def cleanup_audits():
    """Delete all files inside the audits folder without removing the folder itself."""
    deleted_files = []
    skipped_items = []
    errors = []

    try:
        for entry in AUDITS_DIR.iterdir():
            if not entry.is_file():
                skipped_items.append(entry.name)
                continue

            try:
                entry.unlink()
                deleted_files.append(entry.name)
            except OSError as exc:
                errors.append(f"{entry.name}: {exc}")

        return jsonify({
            "status": "ok",
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "skipped_items": skipped_items,
            "errors": errors,
        }), 200

    except Exception as exc:
        logger.exception("Error cleaning audits folder")
        return jsonify({"error": str(exc)}), 500


@app.errorhandler(404)
def not_found(_):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def server_error(_):
    return jsonify({"error": "Internal server error"}), 500


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    logger.info("Starting Ticket Audit API — http://localhost:5000")
    app.run(debug=True, port=5000, threaded=True)
