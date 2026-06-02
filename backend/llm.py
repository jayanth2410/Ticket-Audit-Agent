import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLM:

    def __init__(self):

        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")

        self.client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
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
                max_tokens=20
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

Resolution Notes:
{close_notes}

Work Notes:
{work_notes_text}

You are reviewing service desk resolution notes.

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