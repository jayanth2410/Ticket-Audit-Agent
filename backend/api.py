"""
api.py — Flask API server for the Ticket Audit UI
===================================================
Place this file in ticketaudit/backend/ alongside the other backend files.

Install deps:
    pip install flask flask-cors python-dotenv requests openpyxl openai

Run:
    cd ticketaudit/backend
    python api.py

Then open  ticketaudit/frontend/index.html  in your browser.
"""

import os
import sys
import json
import uuid
import threading
import queue
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from flask import Flask, Response, request, jsonify, send_file
from flask_cors import CORS
from dotenv import load_dotenv

# ── Path setup ────────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent
sys.path.insert(0, str(BACKEND_DIR))

load_dotenv(BACKEND_DIR.parent / ".env")

from incident_fetcher import IncidentFetcher
from auditor import Auditor
from excel_handler import ExcelHandler

app  = Flask(__name__)
CORS(app)

# ── Credentials ───────────────────────────────────────────────────────────────
SERVICENOW_INSTANCE = os.getenv("SERVICENOW_INSTANCE")
SERVICENOW_USER     = os.getenv("SERVICENOW_USER")
SERVICENOW_PASSWORD = os.getenv("SERVICENOW_PASSWORD")
TEMPLATE_PATH       = str(BACKEND_DIR.parent / "Audit_Report_Template.xlsx")

# ── In-memory job store ───────────────────────────────────────────────────────
JOBS: dict = {}

# ── Metric → max points ───────────────────────────────────────────────────────
METRIC_SCORES = {
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

PASS_THRESHOLD = 80  # percent


# ─────────────────────────────────────────────────────────────────────────────
# Scoring helper
# ─────────────────────────────────────────────────────────────────────────────

def compute_score(audit_data: dict) -> dict:
    """Compute score / out_of / percentage / quality_result for one ticket."""
    score  = 0
    out_of = 0

    for metric, max_pts in METRIC_SCORES.items():
        value = audit_data.get(metric, "NA")
        if value == "NA":
            score  += max_pts
        out_of += max_pts
        if value == "Yes":
            score  += max_pts

    percentage     = round(score / out_of * 100, 1) if out_of > 0 else 0
    quality_result = "Pass" if percentage >= PASS_THRESHOLD else "Fail"

    return {
        "score"         : score,
        "out_of"        : out_of,
        "percentage"    : percentage,
        "quality_result": quality_result,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Background audit worker
# ─────────────────────────────────────────────────────────────────────────────

def run_audit_job(job_id: str, ticket_type: str, start_date: str, end_date: str, resolver_group: str):
    """Full fetch + audit pipeline. Runs in a background thread."""
    job   = JOBS[job_id]
    log_q = job["log_queue"]
    results = []

    def log(msg: str):
        print(msg)        # also print to terminal
        log_q.put(msg)

    try:
        # ── Step 1: Fetch incidents ───────────────────────────────────────────
        log(f"Connecting to ServiceNow: {SERVICENOW_INSTANCE}")
        fetcher = IncidentFetcher(SERVICENOW_INSTANCE, SERVICENOW_USER, SERVICENOW_PASSWORD, log_callback=log)

        log(f"Fetching closed {ticket_type} from {start_date} to {end_date}...")
        incidents = fetcher.fetch_incidents_in_range(
            ticket_type,
            start_date,
            end_date,
            resolver_group=resolver_group or None,
        )

        total = len(incidents)
        log(f"Found {total} incident(s)")

        if total == 0:
            log("No incidents found for the given date range.")
            job["status"]  = "done"
            job["results"] = {
                "tickets": [],
                "summary": {"total": 0, "passed": 0, "failed": 0, "pass_pct": 0, "avg_pct": 0},
                "metrics": {},
            }
            log_q.put("__DONE__")
            return

        # ── Step 2: Init Excel handler ────────────────────────────────────────
        output_path = str(BACKEND_DIR.parent / f"Audit_Report_{job_id}.xlsx")
        excel = ExcelHandler(TEMPLATE_PATH, output_path)
        job["excel_path"] = output_path

        # ── Step 3: Audit each incident ───────────────────────────────────────
        log(f"Starting audit for {total} ticket(s)...")

        metric_yes   = {m: 0 for m in METRIC_SCORES}
        metric_total = {m: 0 for m in METRIC_SCORES}

        for idx, incident in enumerate(incidents, 1):
            number = incident.get("number", f"#{idx}")
            log(f"[{idx}/{total}] Auditing {number}...")

            auditor    = Auditor(incident)
            audit_data = auditor.get_audit_data()
            scores     = compute_score(audit_data)

            # Write to Excel
            excel.write_audit_row(audit_data)

            # Accumulate metric stats
            for metric in METRIC_SCORES:
                val = audit_data.get(metric, "NA")
                if val != "NA":
                    metric_total[metric] += 1
                    if val == "Yes":
                        metric_yes[metric] += 1

            results.append({
                "ticket_number" : audit_data.get("ticket_number", ""),
                "created_by"    : audit_data.get("created_by",    ""),
                "priority"      : audit_data.get("priority",      ""),
                "resolver_group": audit_data.get("tcs_resolver_group", ""),
                "resolved_by"   : audit_data.get("resolved_by",   ""),
                "metrics"       : {m: audit_data.get(m, "NA") for m in METRIC_SCORES},
                **scores,
            })

            log(f"  ✓ {number} — {scores['percentage']}% ({scores['quality_result']})")

        # ── Step 4: Build summary ─────────────────────────────────────────────
        passed   = sum(1 for r in results if r["quality_result"] == "Pass")
        failed   = total - passed
        pass_pct = round(passed / total * 100, 1) if total > 0 else 0
        avg_pct  = round(sum(r["percentage"] for r in results) / total, 1) if total > 0 else 0

        metrics_summary = {
            m: {
                "yes"     : metric_yes[m],
                "total"   : metric_total[m],
                "pass_pct": round(metric_yes[m] / metric_total[m] * 100, 1)
                            if metric_total[m] > 0 else 0,
            }
            for m in METRIC_SCORES
        }

        job["results"] = {
            "tickets": results,
            "summary": {
                "total"   : total,
                "passed"  : passed,
                "failed"  : failed,
                "pass_pct": pass_pct,
                "avg_pct" : avg_pct,
            },
            "metrics": metrics_summary,
        }
        job["status"] = "done"

        log(f"Audit complete — {passed}/{total} passed ({pass_pct}%)")
        log(f"Report saved → Audit_Report_{job_id}.xlsx")

    except Exception as e:
        log(f"ERROR: {e}")
        job["status"]  = "error"
        job["results"] = None

    finally:
        log_q.put("__DONE__")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/run-audit", methods=["POST"])
def run_audit():
    """Start an audit job. Returns job_id immediately."""
    body           = request.json or {}
    ticket_type    = body.get("ticket_type", "incident").strip()
    start_date     = body.get("start_date", "").strip()
    end_date       = body.get("end_date",   "").strip()
    resolver_group = body.get("resolver_group", "").strip()

    if not start_date or not end_date:
        return jsonify({"error": "start_date and end_date are required"}), 400

    if ticket_type not in ["incident", "service_request", "change_request"]:
        return jsonify({"error": "Invalid ticket_type"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "log_queue" : queue.Queue(),
        "status"    : "running",
        "results"   : None,
        "excel_path": None,
    }

    threading.Thread(
        target=run_audit_job,
        args=(job_id, ticket_type, start_date, end_date, resolver_group),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/api/stream/<job_id>")
def stream(job_id: str):
    """SSE — streams log lines as they are produced by the worker thread."""
    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404

    def event_stream():
        log_q = JOBS[job_id]["log_queue"]
        while True:
            msg = log_q.get()
            if msg == "__DONE__":
                yield "data: __DONE__\n\n"
                break
            # Escape newlines so SSE stays valid
            safe = msg.replace("\n", " ")
            yield f"data: {safe}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control"              : "no-cache",
            "X-Accel-Buffering"          : "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/results/<job_id>")
def get_results(job_id: str):
    """Return final results once the job is done."""
    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404

    job = JOBS[job_id]
    if job["status"] == "running":
        return jsonify({"status": "running"}), 202

    return jsonify({"status": job["status"], "results": job["results"]})


@app.route("/api/download/<job_id>")
def download(job_id: str):
    """Download the generated Excel report."""
    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404

    excel_path = JOBS[job_id].get("excel_path")
    if not excel_path or not os.path.exists(excel_path):
        return jsonify({"error": "Report not ready yet"}), 404

    return send_file(
        excel_path,
        as_attachment=True,
        download_name="Audit_Report.xlsx",
    )


@app.route("/api/send-email", methods=["POST"])
def send_email():
    """Send audit report via email with insights."""
    body = request.json or {}
    job_id = body.get("job_id")
    start_date = body.get("start_date")
    end_date = body.get("end_date")
    summary = body.get("summary", {})

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    if job_id not in JOBS:
        return jsonify({"error": "Job not found"}), 404

    excel_path = JOBS[job_id].get("excel_path")
    if not excel_path or not os.path.exists(excel_path):
        return jsonify({"error": "Report not ready"}), 404

    try:
        # Get recipient email from environment
        recipient_email = os.getenv("RECIPIENT_EMAIL")
        if not recipient_email:
            return jsonify({"error": "Recipient email not configured"}), 500

        # Create email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Ticket Audit Report"
        msg["From"] = os.getenv("SMTP_FROM", "noreply@ticketaudit.local")
        msg["To"] = recipient_email

        # Format insights for email
        total = summary.get("total", 0)
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        pass_rate = round((passed / total * 100), 1) if total > 0 else 0

        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
              <h2 style="color: #16a34a; margin-bottom: 24px;">✓ Ticket Audit Report</h2>
              
              <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin-bottom: 24px;">
                <h3 style="color: #1f2937; margin-top: 0;">Audit Range</h3>
                <p style="margin: 8px 0;"><strong>Start Date:</strong> {start_date}</p>
                <p style="margin: 8px 0;"><strong>End Date:</strong> {end_date}</p>
              </div>

              <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 24px;">
                <div style="background: #e0f2fe; padding: 16px; border-radius: 8px; text-align: center;">
                  <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px 0; text-transform: uppercase;">Total Audited</p>
                  <p style="color: #2563eb; font-size: 32px; font-weight: bold; margin: 0;">{total}</p>
                </div>
                <div style="background: #dcfce7; padding: 16px; border-radius: 8px; text-align: center;">
                  <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px 0; text-transform: uppercase;">Passed</p>
                  <p style="color: #16a34a; font-size: 32px; font-weight: bold; margin: 0;">{passed}</p>
                </div>
                <div style="background: #fee2e2; padding: 16px; border-radius: 8px; text-align: center;">
                  <p style="color: #6b7280; font-size: 12px; margin: 0 0 8px 0; text-transform: uppercase;">Failed</p>
                  <p style="color: #dc2626; font-size: 32px; font-weight: bold; margin: 0;">{failed}</p>
                </div>
              </div>

              <div style="background: #f1f3f5; padding: 16px; border-radius: 8px; border-left: 4px solid #16a34a;">
                <p style="color: #1f2937; font-weight: bold; margin-top: 0;">Pass Rate: <span style="color: #16a34a; font-size: 18px;">{pass_rate}%</span></p>
              </div>

              <p style="color: #6b7280; font-size: 12px; margin-top: 32px;">The Excel report is attached to this email with detailed audit information.</p>
            </div>
          </body>
        </html>
        """

        part = MIMEText(html_body, "html")
        msg.attach(part)

        # Attach Excel file
        with open(excel_path, "rb") as attachment:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(attachment.read())

        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename= Audit_Report_{job_id}.xlsx")
        msg.attach(part)

        # Send email
        smtp_server = os.getenv("SMTP_SERVER", "localhost")
        smtp_port = int(os.getenv("SMTP_PORT", "25"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASSWORD", "")

        # Use SMTP_SSL for port 465, SMTP for port 587
        try:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                server.starttls()
            
            if smtp_user and smtp_pass:
                server.login(smtp_user, smtp_pass)
            
            server.send_message(msg)
            server.quit()
        except smtplib.SMTPAuthenticationError as e:
            return jsonify({"error": f"Email authentication failed: {str(e)}"}), 500
        except smtplib.SMTPException as e:
            return jsonify({"error": f"SMTP error: {str(e)}"}), 500
        except TimeoutError:
            return jsonify({"error": f"Connection timeout. Check firewall - port {smtp_port} may be blocked."}), 500
        except Exception as e:
            return jsonify({"error": f"Connection failed: {str(e)}. Try disabling VPN/proxy."}), 500

        return jsonify({"success": True, "message": f"Report sent to {recipient_email}"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("Starting Ticket Audit API on http://localhost:5000")
    app.run(debug=True, port=5000, threaded=True)
