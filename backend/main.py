"""
main.py — Ticket Audit Agent API
==================================
Run:
    cd backend
    python main.py

Endpoints:
    POST /api/generate-report   — start an audit job
    GET  /api/report-status/<job_id>
    GET  /api/report-stream/<job_id>   — SSE log stream
    GET  /api/report-results/<job_id>
    POST /api/cancel-report/<job_id>
    GET  /api/download-report/<job_id>
    POST /api/cleanup-audits
    GET  /api/health

Job state (status, logs, cancel signal) is stored in Redis so any
Gunicorn worker can serve any request for any job.
"""

import os
import sys
import json
import logging
import multiprocessing as mp
import time
import uuid
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
from db_config             import DBConfig
from auditor               import Auditor
from excel_handler         import ExcelHandler
from redis_job_store       import RedisJobStore, RedisUnavailableError

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
AUDITS_DIR    = ROOT_DIR / "audits"
DEFAULT_THRESHOLD = 70.0

# ── Redis config (read from .env with sensible defaults) ──────────────────────
REDIS_HOST     = os.getenv("REDIS_HOST",     "localhost")
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB       = int(os.getenv("REDIS_DB",   "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)   # None means no auth

# ── Shared Redis job store ────────────────────────────────────────────────────
#
# One instance per Flask worker process.  Each worker gets its own TCP
# connection to Redis.  All workers see the same data because they all
# point at the same Redis server.
#
store = RedisJobStore(
    host     = REDIS_HOST,
    port     = REDIS_PORT,
    db       = REDIS_DB,
    password = REDIS_PASSWORD,
)
logger.info(f"Redis store created — host={REDIS_HOST} port={REDIS_PORT} db={REDIS_DB} ping={store.ping()}")

# ── Local process registry ────────────────────────────────────────────────────
#
# This dict only tracks the Process object so we can join/terminate it as a
# hard-stop fallback.  It lives in-process (like the old JOBS dict) but that
# is fine: terminate() only needs to run in the worker that started the process,
# and the cooperative cancel (Redis key) handles cross-worker cancellation.
#
_PROCESSES: dict = {}   # job_id -> mp.Process

# ── Metric definitions ────────────────────────────────────────────────────────
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

AUDITS_DIR.mkdir(exist_ok=True)
MP_CTX = mp.get_context("spawn")


class JobCancelled(Exception):
    """Raised when a running audit job is cancelled by the user."""
    pass

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False)


# =============================================================================
# Helpers  (unchanged from original — pure data logic, no job-store calls)
# =============================================================================

def _incident_orm_to_dict(incident) -> dict:
    def _dt(val):
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

    audit_history_dicts = []
    for ah in (incident.audit_history or []):
        if hasattr(ah, "fieldname"):
            audit_history_dicts.append({
                "fieldname"      : _str(ah.fieldname),
                "oldvalue"       : _str(ah.oldvalue),
                "newvalue"       : _str(ah.newvalue),
                "sys_created_by" : _str(ah.sys_created_by),
                "sys_created_on" : _dt(ah.sys_created_on),
            })
        elif isinstance(ah, dict):
            audit_history_dicts.append(ah)

    return {
        "sys_id"              : _str(incident.sys_id),
        "number"              : _str(incident.number),
        "opened_by"           : _str(incident.opened_by),
        "priority"            : _str(incident.priority),
        "u_tcs_resolver_group": _str(incident.u_tcs_resolver_group),
        "assignment_group"    : _str(incident.assignment_group),
        "resolved_by"         : _str(incident.resolved_by),
        "short_description"   : _str(incident.short_description),
        "description"         : _str(incident.description),
        "state"               : _str(incident.state),
        "hold_reason"         : _str(incident.hold_reason),
        "opened_at"           : _dt(incident.opened_at),
        "closed_at"           : _dt(incident.closed_at),
        "resolved_at"         : _dt(incident.resolved_at),
        "reopened_time"       : _dt(incident.reopened_time),
        "reassignment_count"  : _int(incident.reassignment_count),
        "reopen_count"        : _int(incident.reopen_count),
        "sla_data"            : incident.sla_data or {},
        "close_notes"         : _str(incident.close_notes),
        "close_code"          : _str(incident.close_code),
        "work_notes"          : _str(incident.work_notes),
        "comments"            : _str(incident.comments),
        "comments_and_work_notes": _str(incident.comments_and_work_notes),
        "knowledge"           : incident.knowledge,
        "audit_history"       : audit_history_dicts,
        "urgency"             : _str(incident.urgency),
        "severity"            : _str(incident.severity),
        "impact"              : _str(incident.impact),
        "category"            : _str(incident.category),
        "assigned_to"         : _str(incident.assigned_to),
        "caller_id"           : _str(incident.caller_id),
        "company"             : _str(incident.company),
    }


def _compute_score(audit_data: dict, threshold: float) -> dict:
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
    return {"score": score, "out_of": out_of, "percentage": percentage, "quality_result": quality_result}


def _build_metrics_summary(tickets: list) -> dict:
    yes = {m: 0 for m in METRIC_MAX_SCORES}
    no  = {m: 0 for m in METRIC_MAX_SCORES}
    na  = {m: 0 for m in METRIC_MAX_SCORES}
    for t in tickets:
        for metric, val in t.get("metrics", {}).items():
            if val == "Yes":   yes[metric] += 1
            elif val == "No":  no[metric]  += 1
            else:              na[metric]  += 1
    summary = {}
    for metric in METRIC_MAX_SCORES:
        applicable = yes[metric] + no[metric]
        pass_pct   = round(yes[metric] / applicable * 100, 1) if applicable else 0.0
        summary[metric] = {
            "yes": yes[metric], "no": no[metric], "na": na[metric],
            "applicable": applicable, "pass_pct": pass_pct,
            "max_score": METRIC_MAX_SCORES[metric],
        }
    return summary


def _build_observation(audit_data: dict) -> str:
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
            "total": 0, "passed": 0, "failed": 0, "errors": 0,
            "pass_pct": 0.0, "avg_score_pct": 0.0, "threshold": threshold,
        },
        "metrics_summary": {},
        "orchestration": {
            "new"      : analysis.get("new_count", 0),
            "modified" : analysis.get("modified_count", 0),
            "unchanged": analysis.get("unchanged_count", 0),
        },
    }


# =============================================================================
# Core audit runner (runs in a separate spawned process per job)
# =============================================================================

def _run_audit(
    job_id        : str,
    start_date    : str,
    end_date      : str,
    resolver_group: str,
    threshold     : float,
    record_path   : str,
    redis_host    : str,
    redis_port    : int,
    redis_db      : int,
    redis_password,   # str or None
):
    """
    Full audit pipeline — runs in its own spawned process.
    Communicates with the parent (Flask) exclusively through Redis.
    """
    # On Windows, spawn creates a fresh interpreter that re-imports main.py.
    # load_dotenv() runs at module level so env vars are available, but we
    # explicitly reload here as a safety net.
    load_dotenv(ROOT_DIR / ".env")

    # Each spawned process creates its own Redis connection.
    sub_store = RedisJobStore(
        host     = redis_host,
        port     = redis_port,
        db       = redis_db,
        password = redis_password,
    )

    def log(msg: str):
        ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fmt = f"[{ts}] {msg}"
        print(fmt, flush=True)   # always print — logger may not be configured in subprocess
        logger.info(msg)
        try:
            sub_store.push_log(job_id, fmt)
        except Exception as push_err:
            print(f"[log push failed] {push_err}", flush=True)

    def cancel_check() -> bool:
        return sub_store.is_cancelled(job_id)

    def raise_if_cancelled():
        if cancel_check():
            raise JobCancelled("Audit cancelled by user")

    tickets = []

    try:
        raise_if_cancelled()

        # ── 1. Init ───────────────────────────────────────────────────────────
        log("Initialising components...")
        fetcher      = IncidentFetcher(SN_INSTANCE, SN_USER, SN_PASSWORD, log_callback=log)
        db_config    = DBConfig()
        orchestrator = IncidentOrchestrator(db_config, fetcher)

        raise_if_cancelled()

        # ── 2. Fetch + store ──────────────────────────────────────────────────
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

        raise_if_cancelled()

        # ── 3. Load from DB ───────────────────────────────────────────────────
        log("Loading incidents from database...")
        db_result    = orchestrator.get_incidents_in_database(start_date, end_date)
        db_incidents = db_result["incidents"]
        total        = db_result["count"]
        log(f"Loaded {total} incident(s) from database for this range")

        audit_input = []
        if total > 0:
            audit_input = [
                {"number": inc.number, "incident_dict": _incident_orm_to_dict(inc)}
                for inc in db_incidents.values()
            ]
            total = len(audit_input)
        else:
            fetched_incidents = [
                inc for inc in (orch_result.get("fetched_incidents") or [])
                if isinstance(inc, dict)
            ]
            if fetched_incidents:
                log(f"No DB incidents; using {len(fetched_incidents)} freshly fetched.")
                audit_input = [
                    {"number": inc.get("number", "UNKNOWN"), "incident_dict": inc}
                    for inc in fetched_incidents
                ]
                total = len(audit_input)

        raise_if_cancelled()

        if not audit_input:
            log("No incidents found for the given filters.")
            sub_store.finish_job(job_id, "done", results=_build_empty_result(threshold, analysis))
            sub_store.push_log(job_id, "__DONE__")
            return

        # ── 4. Excel setup ────────────────────────────────────────────────────
        excel_path = str(AUDITS_DIR / f"Audit_Report_{job_id}.xlsx")
        log(f"Initialising Excel report → {excel_path}")
        excel = ExcelHandler(TEMPLATE_PATH, excel_path, pass_threshold=threshold)
        sub_store.set_job_field(job_id, "excel_path", excel_path)

        # ── 5. Audit loop ─────────────────────────────────────────────────────
        log(f"Starting audit for {total} incident(s)...")

        for idx, item in enumerate(audit_input, 1):
            raise_if_cancelled()
            number = item["number"]
            try:
                incident_dict = item["incident_dict"]
                auditor    = Auditor(incident_dict)
                audit_data = auditor.get_audit_data()
                scores     = _compute_score(audit_data, threshold)
                excel.write_audit_row(audit_data)
                # Single log per ticket — clean progress line, no extra ✓ noise
                log(f"[{idx}/{total}] Audited {number}")
                tickets.append({
                    "ticket_number"    : audit_data.get("ticket_number", number),
                    "created_by"       : audit_data.get("created_by", ""),
                    "priority"         : audit_data.get("priority", ""),
                    "resolver_group"   : audit_data.get("tcs_resolver_group", ""),
                    "resolved_by"      : audit_data.get("resolved_by", ""),
                    "short_description": incident_dict.get("short_description", ""),
                    "metrics"          : {m: audit_data.get(m, "NA") for m in METRIC_MAX_SCORES},
                    "score"            : scores["score"],
                    "out_of"           : scores["out_of"],
                    "percentage"       : scores["percentage"],
                    "quality_result"   : scores["quality_result"],
                    "observation"      : _build_observation(audit_data),
                })
            except JobCancelled:
                raise
            except Exception as e:
                logger.exception(f"Error auditing {number}")
                log(f"  ✗ {number} error: {e}")
                tickets.append({
                    "ticket_number": number, "error": str(e),
                    "metrics": {}, "score": 0, "out_of": 0,
                    "percentage": 0.0, "quality_result": "ERROR",
                    "observation": str(e),
                })
            raise_if_cancelled()

        # ── 6. Save Excel ─────────────────────────────────────────────────────
        log("Generating Excel report...")
        excel.save()
        log("Excel report saved")

        # ── 7. Build summary ──────────────────────────────────────────────────
        passed  = sum(1 for t in tickets if t["quality_result"] == "PASS")
        failed  = sum(1 for t in tickets if t["quality_result"] == "FAIL")
        errors  = sum(1 for t in tickets if t["quality_result"] == "ERROR")
        valid   = [t for t in tickets if t["quality_result"] != "ERROR"]
        avg_pct = round(sum(t["percentage"] for t in valid) / len(valid), 1) if valid else 0.0

        results = {
            "status" : "completed",
            "tickets": tickets,
            "summary": {
                "total"         : total,
                "passed"        : passed,
                "failed"        : failed,
                "errors"        : errors,
                "pass_pct"      : round(passed / total * 100, 1) if total else 0.0,
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

        # Write JSON record to disk (unchanged behaviour)
        audit_record = {
            "job_id"     : job_id,
            "status"     : "completed",
            "created_at" : datetime.utcnow().isoformat(),
            "finished_at": datetime.utcnow().isoformat(),
            "params"     : {"start_date": start_date, "end_date": end_date,
                            "resolver_group": resolver_group, "threshold": threshold},
            "results"    : results,
        }
        with open(record_path, "w", encoding="utf-8") as fh:
            json.dump(audit_record, fh, indent=2, default=str)

        sub_store.finish_job(job_id, "done", results=results)
        log(f"Audit complete — {passed} PASS / {failed} FAIL / {errors} ERROR")

    except JobCancelled as e:
        logger.info("Audit job cancelled: %s", job_id)
        log(str(e))
        sub_store.finish_job(job_id, "cancelled", error=str(e))

    except Exception as e:
        import traceback
        err_detail = traceback.format_exc()
        print(f"[SUBPROCESS FATAL] {err_detail}", flush=True)
        logger.exception("Fatal error in audit job")
        log(f"FATAL: {e}")
        sub_store.finish_job(job_id, "error", error=str(e))

    finally:
        job = sub_store.get_job(job_id)
        status = (job or {}).get("status", "error")
        sub_store.push_log(job_id, "__CANCELLED__" if status == "cancelled" else "__DONE__")


# =============================================================================
# API Routes
# =============================================================================

@app.route("/api/health", methods=["GET"])
def health():
    redis_ok = store.ping()
    return jsonify({
        "status"   : "healthy",
        "redis"    : "connected" if redis_ok else "unavailable",
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/generate-report", methods=["POST"])
def generate_report():
    """Start an audit job."""
    try:
        body = request.get_json(force=True) or {}

        start_date     = (body.get("start_date")     or "").strip()
        end_date       = (body.get("end_date")       or "").strip()
        resolver_group = (body.get("resolver_group") or "").strip()

        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required (YYYY-MM-DD)"}), 400

        try:
            datetime.strptime(start_date, "%Y-%m-%d")
            datetime.strptime(end_date,   "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Invalid date format. Use YYYY-MM-DD"}), 400

        try:
            threshold = float(body.get("threshold", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD

        if not (0 <= threshold <= 100):
            return jsonify({"error": "threshold must be between 0 and 100"}), 400

        job_id      = str(uuid.uuid4())[:12]
        excel_path  = str(AUDITS_DIR / f"Audit_Report_{job_id}.xlsx")
        record_path = str(AUDITS_DIR / f"Audit_Record_{job_id}.json")
        params      = {
            "start_date"    : start_date,
            "end_date"      : end_date,
            "resolver_group": resolver_group,
            "threshold"     : threshold,
        }

        # Write job metadata to Redis — visible to ALL workers immediately
        logger.info(f"[generate-report] About to create job {job_id} — store ping: {store.ping()} host={REDIS_HOST} port={REDIS_PORT}")
        store.create_job(job_id, params, excel_path=excel_path, record_path=record_path)

        # Spawn the audit process — passes only serialisable values
        process = MP_CTX.Process(
            target = _run_audit,
            args   = (
                job_id,
                start_date,
                end_date,
                resolver_group,
                threshold,
                record_path,
                REDIS_HOST,
                REDIS_PORT,
                REDIS_DB,
                REDIS_PASSWORD,
            ),
            daemon = True,
        )
        process.start()
        _PROCESSES[job_id] = process

        logger.info(f"Audit job started: {job_id}")

        return jsonify({
            "job_id" : job_id,
            "status" : "running",
            "message": (
                f"Audit started for {start_date} → {end_date}. "
                f"Poll GET /api/report-status/{job_id} to check progress."
            ),
        }), 202

    except RedisUnavailableError as e:
        logger.error("Redis unavailable — cannot start audit job: %s", e)
        return jsonify({"error": f"Redis unavailable: {e}"}), 503
    except Exception as e:
        logger.exception("Error in /api/generate-report")
        return jsonify({"error": str(e)}), 500


@app.route("/api/cancel-report/<job_id>", methods=["POST"])
def cancel_report(job_id: str):
    """Request cancellation of a running audit job."""
    if not store.job_exists(job_id):
        return jsonify({"error": f"Job {job_id} not found"}), 404

    # Step 1: cooperative cancel via Redis (works from any worker)
    store.set_cancel(job_id)
    store.push_log(job_id, "Cancellation requested by user...")

    # Step 2: hard-stop fallback if this worker happens to own the process
    process = _PROCESSES.get(job_id)
    if process and process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)

    store.set_job_field(job_id, "status", "cancelled")
    store.set_job_field(job_id, "error",  "Audit cancelled by user")
    store.set_job_field(job_id, "finished_at", str(time.time()))
    store.push_log(job_id, "__CANCELLED__")

    return jsonify({
        "job_id" : job_id,
        "status" : "cancelled",
        "message": "Cancellation requested",
    }), 202


@app.route("/api/cancel-report/", methods=["POST"])
def cancel_report_active():
    """Cancel the most recently started running job (no job_id in URL)."""
    active_ids = store.list_active_job_ids()
    if not active_ids:
        return jsonify({"error": "No running job found"}), 404
    return cancel_report(active_ids[0])


@app.route("/api/report-status/<job_id>", methods=["GET"])
def report_status(job_id: str):
    """Get the current status of an audit job."""
    job = store.get_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    return jsonify({
        "job_id"   : job_id,
        "status"   : job["status"],
        "params"   : job.get("params", {}),
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/report-results/<job_id>", methods=["GET"])
def report_results(job_id: str):
    """Get the final results of a completed audit job."""
    job = store.get_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    status = job["status"]

    if status == "cancelled":
        return jsonify({"job_id": job_id, "status": "cancelled",
                        "error": job.get("error", "Audit cancelled by user")}), 200

    if status in ("running", "cancelling"):
        return jsonify({"job_id": job_id, "status": status,
                        "message": "Audit still in progress. Try again shortly."}), 202

    if status == "error":
        return jsonify({"job_id": job_id, "status": "error",
                        "error": job.get("error")}), 500

    # status == "done"
    results = job.get("results")

    # Fallback: results not in Redis (e.g. TTL expired) → try disk record
    if not results:
        record_path = job.get("record_path", "")
        if record_path and os.path.exists(record_path):
            try:
                with open(record_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                results = payload.get("results")
            except Exception as exc:
                return jsonify({"job_id": job_id, "status": "error",
                                "error": f"Failed to load audit record: {exc}"}), 500

    return jsonify({
        "job_id"   : job_id,
        "status"   : "completed",
        "results"  : results,
        "timestamp": datetime.utcnow().isoformat(),
    }), 200


@app.route("/api/report-stream/<job_id>", methods=["GET"])
def report_stream(job_id: str):
    """Server-Sent Events stream — real-time log lines while audit runs."""
    if not store.job_exists(job_id):
        return jsonify({"error": f"Job {job_id} not found"}), 404

    def event_stream():
        try:
            while True:
                msg = store.pop_log(job_id, timeout=20)

                if msg is None:
                    # Timeout — send a heartbeat so the browser doesn't close
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
            "Cache-Control"            : "no-cache",
            "X-Accel-Buffering"        : "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/download-report/<job_id>", methods=["GET"])
def download_report(job_id: str):
    job = store.get_job(job_id)
    if not job:
        return jsonify({"error": f"Job {job_id} not found"}), 404

    path = job.get("excel_path", "")
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
    """Delete all files inside the audits folder without removing the folder."""
    deleted_files = []
    skipped_items = []
    errors        = []
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
            "status"       : "ok",
            "deleted_count": len(deleted_files),
            "deleted_files": deleted_files,
            "skipped_items": skipped_items,
            "errors"       : errors,
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
