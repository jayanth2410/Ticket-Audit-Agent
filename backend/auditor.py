"""
Auditor
========
Audits a single ServiceNow incident against quality metrics.

Usage:
    auditor = Auditor(incident_dict)
    result  = auditor.get_audit_data()   # returns dict of Yes / No / NA values

All public audit methods return strictly "Yes" / "No" / "NA".
"""

import re
from datetime import datetime
from typing import Any, Dict, List

from llm import LLM


# ─────────────────────────────────────────────────────────────────────────────
# Shared keyword lists (used across multiple methods)
# ─────────────────────────────────────────────────────────────────────────────

CONTACT_KEYWORDS = [
    # phone
    "called", "call", "phone", "rang", "dialled", "dialed",
    "voicemail", "left message", "phone call", "tried calling",
    # email
    "emailed", "email sent", "sent email", "sent a mail",
    "mail sent", "dropped a mail", "dropped an email",
    # chat / messaging
    "messaged", "chat", "teams", "slack", "whatsapp",
    "im sent", "instant message", "pinged",
    # generic
    "contacted", "reached out", "attempted to contact",
    "tried to reach", "contact attempt", "follow up", "followup",
    "followed up", "notified user", "informed user",
]

USER_AVAILABLE_KEYWORDS = [
    "user responded", "user replied", "user confirmed", "user available",
    "user called back", "spoke with user", "user provided", "user agreed",
    "user verified", "user acknowledged", "connected with user",
    "user was available", "got in touch", "user accepted",
    "user approved", "received response", "got response from user",
]

USER_UNAVAILABLE_KEYWORDS = [
    "no response", "no reply", "user not available", "user unavailable",
    "not reachable", "unreachable", "did not respond",
    "could not reach", "no answer", "user not responding",
]

STRIKE_KEYWORDS = [
    "strike",
    "attempt 1", "attempt 2", "attempt 3",
    "1st attempt", "2nd attempt", "3rd attempt",
    "first attempt", "second attempt", "third attempt",
    "reminder 1", "reminder 2", "reminder 3",
    "follow up 1", "follow up 2", "follow up 3",
    "followup 1", "followup 2", "followup 3",
]

CONFIRMATION_KEYWORDS = [
    "user confirmed", "user verified", "user accepted", "user approved",
    "confirmed by user", "user agreed", "user satisfaction", "user happy",
    "user ok", "issue resolved by user", "user tested", "user checked",
    "user validated", "user signed off", "confirmation received",
    "user acknowledged", "user received and tested", "working fine",
    "resolved with user", "closed with user",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dt(ts: str) -> datetime | None:
    """Parse a ServiceNow timestamp string. Returns None on failure."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Auditor
# ─────────────────────────────────────────────────────────────────────────────

class Auditor:
    """Audits one incident. Pass the enriched incident dict from IncidentFetcher."""

    def __init__(self, incident: Dict[str, Any]):

        # ── Raw journal text (work_notes arrives as a combined string) ────────
        wn_raw = incident.get("work_notes", "") or ""
        cm_raw = incident.get("comments",   "") or ""

        # ── System ────────────────────────────────────────────────────────────
        self.sys_id = incident.get("sys_id", "")

        # ── Audit history — split into two filtered lists ─────────────────────
        audit_history               = incident.get("audit_history", []) or []
        self.priority_audit_history = [r for r in audit_history if r.get("fieldname") in ("priority", "impact", "urgency")]
        self.state_audit_history    = [r for r in audit_history if r.get("fieldname") == "state"]

        # ── Emails ────────────────────────────────────────────────────────────
        self.emails = incident.get("emails", []) or []

        # ── Report header fields ──────────────────────────────────────────────
        self.ticket_number      = incident.get("number", "")
        self.created_by         = incident.get("opened_by", "")
        self.priority           = incident.get("priority", "")
        self.tcs_resolver_group = incident.get("u_tcs_resolver_group", "") or incident.get("assignment_group", "")
        self.resolved_by        = incident.get("resolved_by", "")

        # ── SLA ───────────────────────────────────────────────────────────────
        self.made_sla     = incident.get("made_sla", "false")
        self.sla_due      = incident.get("sla_due", "")
        self.opened_at    = incident.get("opened_at", "")
        self.resolved_at  = incident.get("resolved_at", "")
        self.closed_at    = incident.get("closed_at", "")
        self.calendar_stc = incident.get("calendar_stc", "")
        self.business_stc = incident.get("business_stc", "")
        
        # ── SLA breach data (from task_sla table) ──────────────────────────────
        sla_data = incident.get("sla_data", {}) or {}
        self.response_sla_breached = sla_data.get("response_sla_breached")
        self.resolution_sla_breached = sla_data.get("resolution_sla_breached")

        # ── Description ───────────────────────────────────────────────────────
        self.short_description = incident.get("short_description", "")
        self.description       = incident.get("description", "")
        self.category          = incident.get("category", "")
        self.subcategory       = incident.get("subcategory", "") or ""

        # ── Priority / impact / urgency ───────────────────────────────────────
        self.impact   = incident.get("impact", "")
        self.urgency  = incident.get("urgency", "")
        self.severity = incident.get("severity", "")

        # ── State / pending ───────────────────────────────────────────────────
        self.state          = incident.get("state", "")
        self.incident_state = incident.get("incident_state", "")
        self.hold_reason    = incident.get("hold_reason", "")
        self.active         = incident.get("active", "true")

        # ── Reassignment ──────────────────────────────────────────────────────
        self.reassignment_count = int(incident.get("reassignment_count", 0) or 0)
        self.assigned_to        = incident.get("assigned_to", "")

        # ── Resolution ────────────────────────────────────────────────────────
        self.close_notes = incident.get("close_notes", "")
        self.close_code  = incident.get("close_code",  "") or ""

        # ── Reopen ────────────────────────────────────────────────────────────
        self.reopen_count  = int(incident.get("reopen_count", 0) or 0)
        self.reopened_by   = incident.get("reopened_by",   "")
        self.reopened_time = incident.get("reopened_time", "")

        # ── KBA ───────────────────────────────────────────────────────────────
        self.knowledge = incident.get("knowledge", "false")

        # ── Journal text (for keyword scanning) ───────────────────────────────
        self.work_notes_text  = wn_raw.lower()
        self.comments_text    = cm_raw.lower() if isinstance(cm_raw, str) else ""
        self.all_journal_text = self.work_notes_text + " " + self.comments_text

        # ── Parsed work note entries (for timestamp-based checks) ─────────────
        self.work_notes = self._parse_work_note_entries(wn_raw)
        self.comments   = []

        # ── LLM ───────────────────────────────────────────────────────────────
        self.llm = LLM()

    # ─────────────────────────────────────────────────────────────────────────
    # Internal parser
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_work_note_entries(self, combined: str) -> List[Dict[str, Any]]:
        """
        Parse the combined work notes string from IncidentFetcher back into
        a list of individual entry dicts.

        Fetcher format per entry:
            "[2026-05-26 11:21:04] admin\nnote text\n\n"

        Returns:
            List of { sys_created_on, sys_created_by, value }
        """
        if not combined:
            return []

        pattern = re.compile(
            r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+(.+?)\n(.*?)(?=\[\d{4}-\d{2}-\d{2}|\Z)',
            re.DOTALL
        )

        entries = []
        for match in pattern.finditer(combined):
            entries.append({
                "sys_created_on": match.group(1).strip(),
                "sys_created_by": match.group(2).strip(),
                "value"         : match.group(3).strip(),
            })
        return entries

    # ─────────────────────────────────────────────────────────────────────────
    # Audit methods — return "Yes" / "No" / "NA" only
    # ─────────────────────────────────────────────────────────────────────────

    def responsewithinSLA(self) -> str:
        """
        Was the ticket resolved within SLA?
        Source : made_sla field
        """
        return "Yes" if str(self.made_sla).lower() == "true" else "No"

    def short_desc_quality(self) -> str:
        """
        Is the short description aligned to a user or technical problem?
        Source : LLM analysis on short_description
        """
        return self.llm.short_desc_analyser(self.short_description)

    def is_priority_reassessed(self) -> str:
        """
        Was priority / impact / urgency re-assessed during the lifecycle?
        Source : priority_audit_history (sys_audit)
        """
        if not self.priority_audit_history:
            return "NA"

        for record in self.priority_audit_history:
            if (record.get("oldvalue") or "").strip() != (record.get("newvalue") or "").strip():
                return "Yes"

        return "No"

    def is_incident_reassigned(self) -> str:
        """
        Was the incident re-assigned and were details documented in work notes?
        Source : reassignment_count field + all_journal_text keyword scan
        """
        if self.reassignment_count <= 0:
            return "NA"

        reassignment_keywords = [
            "reassigned to", "reassigned", "re-assigned", "reassign to",
            "assigned to", "transferred to", "transfer to",
            "hand over", "handed over", "handover", "handoff",
            "escalated to", "escalated", "routed to", "forwarded to",
            "delegated to", "passed to", "moved to",
            "change group", "change team", "change ownership",
        ]

        if any(kw in self.all_journal_text for kw in reassignment_keywords):
            return "Yes"

        return "No"

    def check_user_contact(self) -> str:
        """
        Did the associate contact the user for additional information?
        Source : work_notes_text keyword scan + outbound/inbound emails

        Logic:
            No contact found              → NA
            Contact found + user replied  → Yes
            Contact found + user absent:
                3+ attempts               → Yes  (3-strike followed)
                < 3 attempts              → No
        """
        wn_text = self.work_notes_text

        contact_in_notes  = any(kw in wn_text for kw in CONTACT_KEYWORDS)
        outbound_emails   = [e for e in self.emails if str(e.get("direction", "")).lower() == "outbound"]
        contact_made      = contact_in_notes or bool(outbound_emails)

        if not contact_made:
            return "NA"

        inbound_emails = [e for e in self.emails if str(e.get("direction", "")).lower() == "inbound"]
        user_available = any(kw in wn_text for kw in USER_AVAILABLE_KEYWORDS) or bool(inbound_emails)

        if user_available:
            return "Yes"

        # Count attempts — take the highest signal
        strike_count   = sum(1 for kw in STRIKE_KEYWORDS if kw in wn_text)
        entry_attempts = sum(1 for e in self.work_notes if any(kw in e.get("value", "").lower() for kw in CONTACT_KEYWORDS))
        final_count    = max(strike_count, entry_attempts, len(outbound_emails))

        return "Yes" if final_count >= 3 else "No"

    def check_pending_status(self) -> str:
        """
        Was the ticket put in Pending and was the correct pending type used?
        Source : state_audit_history + hold_reason field + work_notes_text

        Returns NA if ticket was never put in Pending.
        Returns Yes if pending type was used and correctly documented.
        Returns No if pending was used without proper documentation.
        """
        pending_transitions = [
            e for e in self.state_audit_history
            if "pending" in str(e.get("newvalue", "")).lower()
            or "on hold" in str(e.get("newvalue", "")).lower()
        ]

        if not pending_transitions:
            return "NA"

        pending_type  = str(self.hold_reason or "").strip()
        pending_lower = pending_type.lower()

        # Infer pending type from work notes if hold_reason is empty
        if not pending_lower:
            wn = self.work_notes_text
            if "awaiting vendor" in wn or "vendor action" in wn:
                pending_lower = "vendor"
            elif "awaiting caller" in wn or "awaiting user" in wn:
                pending_lower = "caller"
            elif any(kw in wn for kw in ["chg", "change request", "awaiting change"]):
                pending_lower = "change"
            elif any(kw in wn for kw in ["prb", "problem record", "awaiting problem"]):
                pending_lower = "problem"
            else:
                return "No"  # pending used but no reason documented

        if "vendor" in pending_lower:
            vendor_keywords = [
                "vendor", "supplier", "vendor ticket", "vendor reference",
                "vendor case", "vendor contact", "support case", "vendor update",
                "logged with vendor", "raised with vendor", "vendor notified",
                "vendor escalation", "third party", "oem",
            ]
            return "Yes" if any(kw in self.work_notes_text for kw in vendor_keywords) else "No"

        elif "caller" in pending_lower or "user" in pending_lower:
            caller_keywords = [
                "contacted user", "called user", "emailed user", "reached out",
                "awaiting user response", "waiting for user", "pending user",
                "user not available", "no response from user", "user not responding",
            ]
            return "Yes" if any(kw in self.work_notes_text for kw in caller_keywords) else "No"

        elif "change" in pending_lower:
            return "Yes" if re.search(r'\bchg\d+\b', self.work_notes_text, re.IGNORECASE) else "No"

        elif "problem" in pending_lower:
            return "Yes" if re.search(r'\bprb\d+\b', self.work_notes_text, re.IGNORECASE) else "No"

        return "No"

    def check_work_notes_regular_update(self) -> str:
        """
        Did the associate update work notes regularly throughout the lifecycle?
        Source : parsed work note entry timestamps vs ticket open/close times

        Logic:
            0 entries                        → No
            1 entry, ticket life <= 24h      → Yes
            1 entry, ticket life >  24h      → No
            Multiple entries, avg gap <= 24h → Yes
            Multiple entries, avg gap >  24h → No
        """
        if not self.work_notes:
            return "No"

        timestamps = sorted(filter(None, (_parse_dt(e.get("sys_created_on", "")) for e in self.work_notes)))

        if not timestamps:
            return "NA"

        if len(timestamps) == 1:
            opened = _parse_dt(self.opened_at)
            closed = _parse_dt(self.closed_at)
            if opened and closed:
                life_hours = (closed - opened).total_seconds() / 3600
                return "Yes" if life_hours <= 24 else "No"
            return "No"

        gaps          = [(timestamps[i] - timestamps[i - 1]).total_seconds() / 3600 for i in range(1, len(timestamps))]
        avg_gap_hours = sum(gaps) / len(gaps)
        return "Yes" if avg_gap_hours <= 24 else "No"

    def check_resolution_notes(self) -> str:
        """
        Did the associate document the finding and resolution steps?
        Source : LLM analysis on close_notes + full work_notes list
        """
        return self.llm.resolution_notes_analyser(
            close_notes=self.close_notes,
            work_notes=self.work_notes,
        )

    def check_user_confirmation_before_resolve(self) -> str:
        """
        Did the associate take user confirmation before resolving?
        Source : close_notes + work_notes + emails (all filtered to before resolved_at)

        Logic:
            Confirmation evidence found       → Yes
            User unavailable + 3+ attempts   → Yes  (3-strike followed)
            User unavailable + < 3 attempts  → No
            No evidence at all               → NA
        """
        resolved_dt = _parse_dt(self.resolved_at)

        def before_resolve(ts: str) -> bool:
            if not resolved_dt or not ts:
                return True
            dt = _parse_dt(ts)
            return dt <= resolved_dt if dt else True

        # 1. Quick check on close_notes
        if any(kw in self.close_notes.lower() for kw in CONFIRMATION_KEYWORDS):
            return "Yes"

        # 2. Scan work notes before resolved_at
        confirmed      = False
        unavailable    = False
        attempt_count  = 0

        for entry in self.work_notes:
            value   = entry.get("value", "").lower()
            created = entry.get("sys_created_on", "")

            if not before_resolve(created):
                continue

            if any(kw in value for kw in CONFIRMATION_KEYWORDS):
                confirmed = True
                break
            if any(kw in value for kw in USER_UNAVAILABLE_KEYWORDS):
                unavailable = True
            if any(kw in value for kw in CONTACT_KEYWORDS):
                attempt_count += 1

        if confirmed:
            return "Yes"

        # 3. Inbound email before resolve = user replied
        if any(
            str(e.get("direction", "")).lower() == "inbound" and before_resolve(e.get("sys_created_on", ""))
            for e in self.emails
        ):
            return "Yes"

        # 4. 3-strike check
        email_attempts  = sum(1 for e in self.emails if str(e.get("direction", "")).lower() == "outbound" and before_resolve(e.get("sys_created_on", "")))
        final_attempts  = max(attempt_count, email_attempts)

        if unavailable or email_attempts > 0:
            return "Yes" if final_attempts >= 3 else "No"

        return "NA"

    def check_reopened_and_user_connect(self) -> str:
        """
        Was the ticket re-opened? If yes, did the associate connect with the user?
        Source : reopen_count field + work_notes and emails after reopened_time

        Returns NA if ticket was never reopened.
        """
        if self.reopen_count == 0:
            return "NA"

        reopen_dt = _parse_dt(self.reopened_time)

        def after_reopen(ts: str) -> bool:
            if not reopen_dt or not ts:
                return True
            dt = _parse_dt(ts)
            return dt > reopen_dt if dt else True

        # Check work notes after reopen
        contact_in_notes = any(
            any(kw in entry.get("value", "").lower() for kw in CONTACT_KEYWORDS)
            for entry in self.work_notes
            if after_reopen(entry.get("sys_created_on", ""))
        )

        # Check emails after reopen
        contact_via_email = any(
            str(e.get("direction", "")).lower() in ("outbound", "inbound")
            and after_reopen(e.get("sys_created_on", ""))
            for e in self.emails
        )

        return "Yes" if (contact_in_notes or contact_via_email) else "No"

    def check_kba_education(self) -> str:
        """
        Did the associate educate the user about a KBA / self-help article?
        Source : all_journal_text keyword scan + knowledge flag
        """
        kba_keywords = [
            "kba", "knowledge article", "knowledge base", "kb article",
            "self help", "self-help", "self service", "self-service",
            "refer to article", "please refer", "solution article",
            "shared article", "sent article", "shared kb", "refer kb",
        ]

        if any(kw in self.all_journal_text for kw in kba_keywords):
            return "Yes"

        if str(self.knowledge).lower() == "true":
            return "Yes"

        return "No"

    def check_response_sla(self) -> str:
        """
        Was the response SLA met (not breached)?
        Source : response_sla_breached field from task_sla table
        
        Returns:
            "Yes" if response SLA was not breached
            "No" if response SLA was breached
            "NA" if no SLA data available
        """
        if self.response_sla_breached is None:
            return "NA"
        
        return "Yes" if str(self.response_sla_breached).lower() != "true" else "No"

    def check_resolution_sla(self) -> str:
        """
        Was the resolution SLA met (not breached)?
        Source : resolution_sla_breached field from task_sla table
        
        Returns:
            "Yes" if resolution SLA was not breached
            "No" if resolution SLA was breached
            "NA" if no SLA data available
        """
        if self.resolution_sla_breached is None:
            return "NA"
        
        return "Yes" if str(self.resolution_sla_breached).lower() != "true" else "No"

    # ─────────────────────────────────────────────────────────────────────────
    # Report output
    # ─────────────────────────────────────────────────────────────────────────

    def get_audit_data(self) -> Dict[str, Any]:
        """
        Run all audit checks and return results for the report writer.
        All scoring values are strictly "Yes" / "No" / "NA".
        """
        return {
            # Header columns
            "ticket_number"            : self.ticket_number,
            "created_by"               : self.created_by,
            "priority"                 : self.priority,
            "tcs_resolver_group"       : self.tcs_resolver_group,
            "resolved_by"              : self.resolved_by,

            # Scoring columns
            "response_sla_met"         : self.check_response_sla(),
            "resolution_sla_met"       : self.check_resolution_sla(),
            #"response_within_sla"      : self.responsewithinSLA(),
            "short_desc_quality"       : self.short_desc_quality(),
            "priority_reassessed"      : self.is_priority_reassessed(),
            "incident_reassigned"      : self.is_incident_reassigned(),
            "user_contact"             : self.check_user_contact(),
            "pending_status"           : self.check_pending_status(),
            "work_notes_regular_update": self.check_work_notes_regular_update(),
            "resolution_notes_quality" : self.check_resolution_notes(),
            "user_confirmation"        : self.check_user_confirmation_before_resolve(),
            "reopened_user_connect"    : self.check_reopened_and_user_connect(),
            "kba_education"            : self.check_kba_education(),
        }