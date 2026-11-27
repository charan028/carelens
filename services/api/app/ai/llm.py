"""Claude as the intent classifier, behind the `ConstrainedLLMIntentModel` seam.

One call per utterance, with a JSON schema the API enforces: the model may
only return an intent name from `INTENTS` plus three optional details (the
medicine named, the time of day, a reminder lead). It never writes a reply, a
dose, a time or a payment fact; the safety policy runs before it, and the
rule-based model takes over whenever the call fails or the answer is off-list.
Without credentials the rules are used on their own.

Privacy note: with this on, the text of what the person says is sent to
Anthropic's API. Nothing else (no names, records, or history) is sent.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence

from .intent import INTENTS

log = logging.getLogger("carelens.llm")

SYSTEM = (
    "You classify one thing an older adult said to a medication and bill reminder "
    "assistant. The text usually comes from speech recognition and may contain misheard "
    "or garbled words; infer the most likely meaning. Choose exactly one intent:\n\n"
    "today_briefing: what to do today, this morning, next\n"
    "list_medications: which medicines are on the list\n"
    "medication_education: what a medicine is for or how it works, in general\n"
    "report_taken: they took a medicine\n"
    "report_skipped: they skipped or did not take a medicine\n"
    "report_unsure: not sure whether they took it\n"
    "list_bills: which bills are due\n"
    "create_bill_reminder: remind them before a bill is due\n"
    "report_bill_paid: they paid a bill\n"
    "list_appointments: appointments or doctor visits\n"
    "request_caregiver_alert: tell or alert a family member or caregiver\n"
    "repeat: say that again\n"
    "help: what can you do\n"
    "unknown: anything else\n\n"
    "If they name a medicine, give its real drug name with speech errors corrected "
    "(for example 'lice in a pril' is lisinopril, 'met for men' is metformin). "
    "Never invent a medicine that was not said."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "medicine": {
            "type": ["string", "null"],
            "description": "Drug name the person said, spelling corrected; null if none was named.",
        },
        "period": {
            "type": ["string", "null"],
            "description": "Time of day they referred to: morning, midday, evening or night; else null.",
        },
        "lead_days": {
            "type": ["integer", "null"],
            "description": "For a bill reminder, days before the due date they asked for; else null.",
        },
    },
    "required": ["intent", "medicine", "period", "lead_days"],
    "additionalProperties": False,
}

Completer = Callable[[str, Sequence[str]], dict | str]


def make_completer(model: str, client=None) -> Completer:
    """Build the `complete(text, allowed)` callable the intent seam expects.

    `client` is injectable for tests; by default the SDK resolves credentials
    from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
    """
    if client is None:
        import anthropic

        client = anthropic.Anthropic(timeout=6.0, max_retries=1)

    def complete(text: str, allowed: Sequence[str]) -> dict | str:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=128,
                system=SYSTEM,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": text}],
            )
        except Exception as error:
            # The seam falls back to the rules; say why in the log, type only
            # (the message could contain the utterance).
            log.warning("intent model call failed (%s); rules used", type(error).__name__)
            raise
        if response.stop_reason == "refusal":
            return ""  # not an intent: the seam falls back to the rules
        raw = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("intent model returned non-JSON; rules used")
            return ""
        return data if isinstance(data, dict) else ""

    return complete
