"""
ServiceNow Incident Fetcher
============================
Fetches incidents within a date range along with:
  - Work notes       (sys_journal_field)
  - Audit history    (sys_audit  — priority, impact, urgency, state)
  - Emails           (sys_email)

Usage:
    fetcher  = IncidentFetcher(INSTANCE_URL, USERNAME, PASSWORD)
    incidents = fetcher.fetch_incidents_in_range("2026-05-01", "2026-05-31")
    fetcher.save_to_json(incidents, "incidents.json")
"""

import json
from typing import List, Dict, Any, Optional

import requests
from requests.auth import HTTPBasicAuth


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

CLOSED_STATE   = "7"          # ServiceNow state value for Closed
DEFAULT_LIMIT  = 1000         # Max records per API call


# ─────────────────────────────────────────────────────────────────────────────
# IncidentFetcher
# ─────────────────────────────────────────────────────────────────────────────

class IncidentFetcher:
    """Fetches ServiceNow incidents with all data needed for audit."""

    def __init__(self, instance_url: str, username: str, password: str, log_callback=None):
        """
        Args:
            instance_url : ServiceNow instance URL  e.g. https://dev392253.service-now.com
            username     : ServiceNow username
            password     : ServiceNow password
            log_callback : Optional function(msg) for logging progress
        """
        self.instance_url = instance_url.rstrip("/")
        self.auth         = HTTPBasicAuth(username, password)
        self.headers      = {
            "Accept"      : "application/json",
            "Content-Type": "application/json",
        }
        self.log_callback = log_callback

    # ─────────────────────────────────────────────────────────────────────────
    # Logging helper
    # ─────────────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        """Log a message via callback if provided, otherwise print."""
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    # ─────────────────────────────────────────────────────────────────────────

    def _fetch_work_notes(self, sys_id: str) -> str:
        """
        Fetch all work notes for an incident from sys_journal_field.

        Returns:
            Combined work notes as a formatted string:
            "[2026-05-26 11:21:04] admin\nnote text\n\n"
        """
        url    = f"{self.instance_url}/api/now/table/sys_journal_field"
        params = {
            "sysparm_query"   : f"name=incident^element=work_notes^element_id={sys_id}",
            "sysparm_fields"  : "sys_created_on,sys_created_by,value",
            "sysparm_order_by": "sys_created_on",
            "sysparm_limit"   : DEFAULT_LIMIT,
        }

        try:
            resp = requests.get(url, auth=self.auth, headers=self.headers, params=params, verify=True)
            resp.raise_for_status()
            entries = resp.json().get("result", [])

            combined = ""
            for entry in entries:
                combined += (
                    f"[{entry.get('sys_created_on', 'N/A')}] "
                    f"{entry.get('sys_created_by', 'N/A')}\n"
                    f"{entry.get('value', '')}\n\n"
                )
            return combined.strip()

        except Exception as e:
            print(f"  Warning: Could not fetch work notes for {sys_id}: {e}")
            return ""

    def _fetch_audit_history(self, sys_id: str) -> List[Dict[str, Any]]:
        """
        Fetch field change history for priority, impact, urgency, and state
        from sys_audit table.

        Returns:
            List of dicts — fieldname, oldvalue, newvalue, sys_created_on, sys_created_by
        """
        url    = f"{self.instance_url}/api/now/table/sys_audit"
        params = {
            "sysparm_query"        : (
                f"tablename=incident"
                f"^documentkey={sys_id}"
                f"^fieldnameINpriority,impact,urgency,state"
            ),
            "sysparm_fields"       : "fieldname,oldvalue,newvalue,sys_created_on,sys_created_by",
            "sysparm_display_value": "true",   # return display strings not raw integers
            "sysparm_order_by"     : "sys_created_on",
            "sysparm_limit"        : DEFAULT_LIMIT,
        }

        try:
            resp = requests.get(url, auth=self.auth, headers=self.headers, params=params, verify=True)
            resp.raise_for_status()
            return resp.json().get("result", [])

        except Exception as e:
            print(f"  Warning: Could not fetch audit history for {sys_id}: {e}")
            return []

    def _fetch_emails(self, sys_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all emails sent/received for an incident from sys_email table.

        Returns:
            List of dicts — sys_created_on, direction, recipients, subject, body_text
        """
        url    = f"{self.instance_url}/api/now/table/sys_email"
        params = {
            "sysparm_query"   : f"instance.sys_id={sys_id}",
            "sysparm_fields"  : "sys_created_on,direction,recipients,subject,body_text",
            "sysparm_order_by": "sys_created_on",
            "sysparm_limit"   : DEFAULT_LIMIT,
        }

        try:
            resp = requests.get(url, auth=self.auth, headers=self.headers, params=params, verify=True)
            resp.raise_for_status()
            return resp.json().get("result", [])

        except Exception as e:
            print(f"  Warning: Could not fetch emails for {sys_id}: {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────────
    # Public — main fetch method
    # ─────────────────────────────────────────────────────────────────────────

    def fetch_incidents_in_range(
        self,
        start_date      : str,
        end_date        : str,
        resolver_group  : Optional[str] = None,
        limit           : int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all closed incidents within a date range.
        Enriches each incident with work_notes, audit_history, and emails.

        Args:
            start_date     : "YYYY-MM-DD"  — range start (closed_at)
            end_date       : "YYYY-MM-DD"  — range end   (closed_at)
            resolver_group : Optional filter on u_tcs_resolver_group
            limit          : Max incidents to fetch (default 1000)

        Returns:
            List of incident dicts, each containing:
                - All standard ServiceNow incident fields
                - work_notes     : formatted string
                - audit_history  : list of field change records
                - emails         : list of email records
        """
        url          = f"{self.instance_url}/api/now/table/incident"
        # Add time components for proper date filtering in ServiceNow
        start_datetime = f"{start_date} 00:00:00"
        end_datetime   = f"{end_date} 23:59:59"
        
        query_parts  = [
            f"state={CLOSED_STATE}",
            f"closed_at>={start_datetime}",
            f"closed_at<={end_datetime}",
        ]

        if resolver_group:
            query_parts.append(f"u_tcs_resolver_group={resolver_group}")

        params = {
            "sysparm_query"                : "^".join(query_parts),
            "sysparm_limit"                : limit,
            "sysparm_display_value"        : "true",
            "sysparm_exclude_reference_link": "true",
        }

        try:
            self._log(f"Fetching incidents from {start_date} to {end_date}...")
            resp = requests.get(url, auth=self.auth, headers=self.headers, params=params, verify=True)
            resp.raise_for_status()
            incidents = resp.json().get("result", [])
            self._log(f"Found {len(incidents)} incident(s)")

        except requests.exceptions.RequestException as e:
            self._log(f"Error fetching incidents: {e}")
            return []

        # Enrich each incident with work notes, audit history, and emails
        if len(incidents) > 0:
            self._log(f"Enriching incident data ({len(incidents)} total)...")
            for idx, incident in enumerate(incidents, 1):
                sys_id = incident.get("sys_id")
                number = incident.get("number")

                if not sys_id:
                    continue

                self._log(f"[{idx}/{len(incidents)}] Fetching {number}")
                incident["work_notes"]    = self._fetch_work_notes(sys_id)
                incident["audit_history"] = self._fetch_audit_history(sys_id)
                incident["emails"]        = self._fetch_emails(sys_id)

        return incidents

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence helpers
    # ─────────────────────────────────────────────────────────────────────────

    def save_to_json(self, incidents: List[Dict[str, Any]], filename: str) -> None:
        """Save incidents list to a JSON file."""
        with open(filename, "w") as f:
            json.dump(incidents, f, indent=2)
        print(f"Saved {len(incidents)} incidents to {filename}")

    def load_from_json(self, filename: str) -> List[Dict[str, Any]]:
        """Load incidents list from a JSON file."""
        with open(filename, "r") as f:
            incidents = json.load(f)
        print(f"Loaded {len(incidents)} incidents from {filename}")
        return incidents