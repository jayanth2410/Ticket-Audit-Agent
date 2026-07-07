import json
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLM:

    def __init__(self):

        api_key = os.getenv("GROQ_API_KEY")
        timeout_raw = os.getenv("GROQ_REQUEST_TIMEOUT_SECONDS", "25")

        try:
            self.request_timeout = float(timeout_raw)
        except (TypeError, ValueError):
            self.request_timeout = 25.0

        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=self.request_timeout,
        )

        self.model = "llama-3.3-70b-versatile"

    # ==========================================================
    # Internal LLM Call
    # ==========================================================

    def _call(self, prompt: str) -> str:

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0,
                max_tokens=500,
                timeout=self.request_timeout,
            )

            content = response.choices[0].message.content

            if not content:
                return ""

            return content.strip()

        except Exception:
            return ""

    # ==========================================================
    # Normalize output to Yes / No
    # ==========================================================

    @staticmethod
    def _to_yes_no(raw: str, default: str = "No") -> str:

        if not raw:
            return default

        text = raw.strip().lower()

        if text.startswith("yes"):
            return "Yes"

        if text.startswith("no"):
            return "No"

        return default

    # ==========================================================
    # Short Description Analysis
    # ==========================================================

    def short_desc_analyser(self, short_description: str) -> str:

        if not short_description or not short_description.strip():
            return "No"

        prompt = f"""
Answer ONLY with Yes or No.

Short Description:
"{short_description}"

Return Yes if the issue can be understood.

Return No if it is too generic.

Examples:
Issue -> No
Problem -> No
Error -> No
Help Needed -> No

User cannot access email on mobile -> Yes
Printer not printing -> Yes
VPN connection failing -> Yes

Answer:
"""

        raw = self._call(prompt)

        return self._to_yes_no(raw)

    # ==========================================================
    # Resolution Notes Analysis
    # ==========================================================

    def resolution_notes_analyser(
        self,
        close_notes: str,
        work_notes: list
    ) -> str:

        close_notes = close_notes or ""

        work_note_lines = []

        for item in work_notes:

            if isinstance(item, dict):
                value = item.get("value", "").strip()
            else:
                value = str(item).strip()

            if value:
                work_note_lines.append(value)

        work_notes_text = "\n".join(work_note_lines)

        if not close_notes.strip() and not work_notes_text.strip():
            return "No"

        prompt = f"""
Answer ONLY with Yes or No.

Resolution Notes (close_notes):
{close_notes}

Work Notes:
{work_notes_text}

You are reviewing service desk resolution notes(close_notes).

Be LENIENT.

Return Yes if the notes contain ANY meaningful troubleshooting,
resolution activity, action taken, user communication, validation,
or progress towards resolution.

Examples that should return Yes:

- Reset password and user confirmed access.
- Replaced faulty hard drive.
- Cleared cache and cookies.
- Restarted service.
- User confirmed issue resolved.
- Escalated to network team.
- Provided instructions to user.
- User tested and confirmed working.
- Reinstalled application.
- Account unlocked.
- Investigated issue and found configuration problem.
- Contacted user and gathered additional information.
- Waiting for user confirmation after applying fix.

Return No ONLY when the notes are too vague and provide no useful information.

Examples that should return No:

- Resolved.
- Fixed.
- Done.
- Closed.
- Completed.
- Working now.

without any explanation.

Answer:
"""

        raw = self._call(prompt)

        return self._to_yes_no(raw)

    # ==========================================================
    # Contact Metrics Analyser
    # ==========================================================

    def contact_metrics_analyser(
        self,
        work_notes: list,
        close_notes: str,
        reopen_count: int,
        reopened_time: str,
    ) -> dict:
        """
        Analyse 3 contact-related metrics from work notes in a single LLM call.

        Metrics evaluated:
            1. user_contact             — Did the associate contact the user?
            2. user_confirmation        — Did the associate take user confirmation before resolving?
            3. reopened_user_connect    — If ticket was reopened, did associate reconnect with user?

        Args:
            work_notes    : list of work note entry dicts { sys_created_on, value }
            close_notes   : close_notes field from incident
            reopen_count  : reopen_count field from incident
            reopened_time : reopened_time field from incident

        Returns:
            {
                "user_contact"          : "Yes" / "No" / "NA",
                "user_confirmation"     : "Yes" / "No" / "NA",
                "reopened_user_connect" : "Yes" / "No" / "NA",
            }
        """

        # Build work notes text
        work_notes_text = "\n".join(
            f"[{entry.get('sys_created_on', '')}] {entry.get('value', '').strip()}"
            for entry in work_notes
            if isinstance(entry, dict) and entry.get("value", "").strip()
        ) if work_notes else "No work notes available."

        # Build reopen context
        reopen_context = (
            f"The ticket was reopened {reopen_count} time(s). Reopen time: {reopened_time}."
            if reopen_count > 0
            else "The ticket was never reopened."
        )

        prompt = f"""You are a service desk quality auditor reviewing an IT incident ticket.

Work Notes (chronological):
{work_notes_text}

Resolution Notes (close_notes):
{close_notes or "Not provided."}

Reopen Info:
{reopen_context}

Evaluate the following 3 metrics and respond ONLY in the JSON format shown below.

METRIC 1 — user_contact:
Did the associate contact the user during the ticket lifecycle for any reason?
Contact includes: phone call, email, Teams/Slack message, WhatsApp, or any documented communication attempt.
- Yes  : associate clearly contacted or attempted to contact the user
- No   : no evidence of any contact attempt
- NA   : not applicable (e.g. ticket was auto-generated or no user involvement needed)

METRIC 2 — user_confirmation:
Did the associate get confirmation from the user before resolving/closing the ticket?
- Yes  : user confirmed issue resolved, tested fix, or 3+ contact attempts were made when user was unavailable
- No   : ticket closed without user confirmation and without following 3-strike process
- NA   : no evidence either way

METRIC 3 — reopened_user_connect:
If the ticket was reopened, did the associate reconnect with the user after reopening?
- Yes  : evidence of user contact after the reopen
- No   : ticket was reopened but no user contact found after reopen
- NA   : ticket was never reopened

Rules:
- Base your answers ONLY on what is written in the notes above.
- Do not assume anything that is not documented.
- For user_contact: even one documented attempt counts as Yes.
- For user_confirmation: if user was unreachable and 3+ attempts are documented, answer Yes.

Respond ONLY with this exact JSON, no explanation, no markdown:
{{"user_contact": "Yes/No/NA", "user_confirmation": "Yes/No/NA", "reopened_user_connect": "Yes/No/NA"}}"""

        try:
            raw = self._call(prompt)

            # Strip any accidental markdown
            raw = raw.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)

            def clean(val):
                v = str(val).strip()
                if v.lower().startswith("yes"): return "Yes"
                if v.lower().startswith("no"):  return "No"
                return "NA"

            return {
                "user_contact"          : clean(parsed.get("user_contact",          "NA")),
                "user_confirmation"     : clean(parsed.get("user_confirmation",      "NA")),
                "reopened_user_connect" : clean(parsed.get("reopened_user_connect",  "NA")),
            }

        except Exception as e:
            print(f"  LLM error [contact_metrics]: {e}")
            return {
                "user_contact"          : "NA",
                "user_confirmation"     : "NA",
                "reopened_user_connect" : "NA",
            }