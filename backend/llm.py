
import os
from openai import OpenAI


class LLM:

    def __init__(self):
        self.client = OpenAI(
            api_key=os.environ.get("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = "openai/gpt-oss-20b"   # correct Groq model ID - openai/gpt-oss-20b

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helper
    # ─────────────────────────────────────────────────────────────────────────

    def _call(self, prompt: str, context: str = "") -> str:
        """
        Single entry point for all Groq API calls.

        Args:
            prompt  : The user prompt to send
            context : Optional label for error messages

        Returns:
            Raw stripped response string from the model
        """
        try:
            response = self.client.chat.completions.create(
                model       = self.model,
                messages    = [{"role": "user", "content": prompt}],
                temperature = 0,
                max_tokens  = 10,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"  LLM error [{context}]: {e}")
            return ""

    @staticmethod
    def _to_yes_no(raw: str, default: str = "No") -> str:
        """Normalise any model response to 'Yes' or 'No'."""
        if "yes" in raw.lower():
            return "Yes"
        if "no" in raw.lower():
            return "No"
        return default

    # ─────────────────────────────────────────────────────────────────────────
    # Audit methods
    # ─────────────────────────────────────────────────────────────────────────

    def short_desc_analyser(self, short_description: str) -> str:
        """
        Is the short description clearly aligned to a user or technical problem?

        Evaluation criteria:
            - Does it mention what is not working or what the user needs?
            - Is there enough context to understand the problem?
            - Brief is fine — it does not need to be perfectly worded.
            - Reject only if completely vague (e.g. just "issue" or "problem").

        Args:
            short_description: The incident short_description field

        Returns:
            "Yes" or "No"
        """
        if not short_description or not short_description.strip():
            return "No"

        prompt = f"""You are auditing an IT service desk ticket.

Short Description: "{short_description}"

Does this short description clearly describe either a user problem or a technical problem?

Guidelines:
- A good description mentions what is not working or what the user cannot do.
  Examples: "can't login to GWS", "printer offline", "laptop running slow", "VPN not connecting"
- It does NOT need to be perfectly worded or grammatically correct.
- Brief but specific is acceptable.
- Only answer "No" if it is completely vague with no context, such as just "issue", "problem", or "help needed".

Respond with ONLY "Yes" or "No".

Response:"""

        raw = self._call(prompt, context=f"short_desc [{short_description}]")
        return self._to_yes_no(raw, default="No")

    def resolution_notes_analyser(self, close_notes: str, work_notes: list) -> str:
        """
        Do the resolution notes contain a proper finding and resolution steps?

        Checks close_notes AND all work notes combined — the finding and steps
        can be spread across multiple entries.

        Evaluation criteria:
            1. FINDING   — what was the root cause or diagnosis?
            2. RESOLUTION STEPS — what actions were taken to fix it?

        Args:
            close_notes : close_notes field from the incident (str)
            work_notes  : full list of work note entry dicts { value, ... }

        Returns:
            "Yes" or "No"
        """
        # Build combined work notes text
        work_notes_text = "\n".join(
            f"- {(entry.get('value', '') if isinstance(entry, dict) else entry).strip()}"
            for entry in work_notes
            if (entry.get('value', '') if isinstance(entry, dict) else entry).strip()
        )

        # Quick reject — nothing to analyse
        if not (close_notes or "").strip() and not work_notes_text.strip():
            return "No"

        prompt = f"""You are auditing an IT service desk incident resolution.

Resolution Notes (close_notes):
"{close_notes or 'Not provided.'}"

Work Notes (chronological):
{work_notes_text if work_notes_text else "No work notes available."}

Does the above contain BOTH of the following across ANY of the notes combined?

1. FINDING — An explanation of what the root cause or diagnosis was.
   Examples: "corrupted driver", "disk full", "account locked", "hardware failure", "misconfigured DNS"

2. RESOLUTION STEPS — The actual actions taken to fix the issue.
   Examples: "reinstalled driver", "cleared disk space", "unlocked account", "replaced hardware", "updated DNS record"

Guidelines:
- The finding and resolution steps can be spread across close_notes and work notes together.
- Brief but specific is acceptable. "Driver reinstalled, user confirmed working" qualifies.
- Only answer "No" if there is genuinely NO explanation of what was done or why.
- Vague statements alone like "resolved", "fixed", "system appears normal", "issue no longer present" do NOT qualify.

Respond with ONLY "Yes" or "No".

Response:"""

        raw = self._call(prompt, context=f"resolution_notes")
        return self._to_yes_no(raw, default="No")