import json
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLM:

    def __init__(self):

        api_key = os.getenv("GROQ_API_KEY")
        timeout_raw = os.getenv("GROQ_REQUEST_TIMEOUT_SECONDS", "25")
        max_tokens_raw = os.getenv("GROQ_MAX_TOKENS", "1000")

        try:
            self.request_timeout = float(timeout_raw)
        except (TypeError, ValueError):
            self.request_timeout = 25.0

        try:
            self.max_tokens = max(100, int(max_tokens_raw))
        except (TypeError, ValueError):
            self.max_tokens = 1000

        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=self.request_timeout,
        )

        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

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
                max_completion_tokens=self.max_tokens,
                reasoning_effort="low",
                response_format={"type": "json_object"},
                timeout=self.request_timeout,
            )

            content = response.choices[0].message.content

            if not content:
                return ""

            return content.strip()

        except Exception as e:
            print(
                f"  LLM request failed: type={type(e).__name__}, "
                f"status={getattr(e, 'status_code', None)}, error={e}",
                flush=True,
            )
            return ""

    # ==========================================================
    # Combined Ticket Metrics Analyser
    # ==========================================================

    def ticket_metrics_analyser(
        self,
        short_description: str,
        work_notes: list,
        close_notes: str,
        reopen_count: int,
        reopened_time: str,
    ) -> dict:
        """Analyse all five LLM-based metrics in one request."""

        work_notes_text = "\n".join(
            f"[{entry.get('sys_created_on', '')}] {entry.get('value', '').strip()}"
            for entry in work_notes
            if isinstance(entry, dict) and entry.get("value", "").strip()
        ) if work_notes else "No work notes available."

        reopen_context = (
            f"The ticket was reopened {reopen_count} time(s). "
            f"Reopen time: {reopened_time or 'Not provided'}."
            if reopen_count > 0
            else "The ticket was never reopened."
        )

        prompt = f"""You are a service desk quality auditor reviewing one IT incident ticket.

Short Description:
{short_description or "Not provided."}

Work Notes (chronological):
{work_notes_text}

Resolution Notes (close_notes):
{close_notes or "Not provided."}

Reopen Info:
{reopen_context}

Evaluate all five metrics using only the information above.

1. short_desc_quality
- Yes: the short description communicates an understandable user or technical issue. Dont be too strict.
- No: it is missing or too generic, such as "Issue", "Problem", "Error", or "Help Needed".

2. resolution_notes_quality
- Yes: the work notes or resolution notes contain any meaningful troubleshooting,
  resolution activity, action taken, communication, validation, or progress.
- No: notes are absent or only say vague things such as "Resolved", "Fixed", or "Done"
  without useful details. Be lenient when meaningful activity is documented.

3. user_contact
- First determine whether the associate needed to contact the user for additional
  information or clarification.
- Yes: contact for additional information is documented and either:
  (a) the user responded and the associate continued the incident process, or
  (b) the user did not respond and at least three separate contact attempts by the
      associate are documented.
- No: additional information was needed but no contact is documented, or the user did
  not respond and fewer than three separate contact attempts are documented.
- NA: no additional information or clarification from the user was needed. Do not mark
  ordinary resolution confirmation alone as contact for additional information.

4. user_confirmation
- Yes: the user confirmed resolution/test success, or at least three unsuccessful
  contact attempts are documented before closure.
- No: the ticket was closed without confirmation and without three attempts.
- NA: the notes do not provide enough evidence to evaluate it.

5. reopened_user_connect
- Yes: user contact is documented after the ticket reopened.
- No: the ticket reopened but no later user contact is documented.
- NA: the ticket was never reopened.

Do not assume facts that are not documented. Respond only with valid JSON, with no
markdown or explanation, using exactly these keys:
{{"short_desc_quality":"Yes/No","resolution_notes_quality":"Yes/No","user_contact":"Yes/No/NA","user_confirmation":"Yes/No/NA","reopened_user_connect":"Yes/No/NA"}}"""

        defaults = {
            "short_desc_quality": "No",
            "resolution_notes_quality": "No",
            "user_contact": "NA",
            "user_confirmation": "NA",
            "reopened_user_connect": "NA",
        }

        try:
            raw = self._call(prompt)
            raw = raw.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(raw)

            def clean(value, default, allow_na):
                normalized = str(value).strip().lower()
                if normalized.startswith("yes"):
                    return "Yes"
                if normalized.startswith("no"):
                    return "No"
                if allow_na and normalized in {"na", "n/a", "not applicable"}:
                    return "NA"
                return default

            return {
                "short_desc_quality": clean(
                    parsed.get("short_desc_quality"), "No", False
                ),
                "resolution_notes_quality": clean(
                    parsed.get("resolution_notes_quality"), "No", False
                ),
                "user_contact": clean(parsed.get("user_contact"), "NA", True),
                "user_confirmation": clean(
                    parsed.get("user_confirmation"), "NA", True
                ),
                "reopened_user_connect": clean(
                    parsed.get("reopened_user_connect"), "NA", True
                ),
            }
        except Exception as e:
            print(f"  LLM error [ticket_metrics]: {e}", flush=True)
            return defaults
