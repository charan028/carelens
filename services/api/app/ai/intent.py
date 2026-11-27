"""Structured command classifier.

Turns an utterance into one intent from a closed list. The default model is
rule-based and needs no network. An LLM can be plugged in behind the same
`IntentModel` interface, and by design all it can return is a member of
`INTENTS`: it never writes a reply, a dose, a time or a payment fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

INTENTS = (
    "today_briefing",
    "list_medications",
    "medication_education",
    "report_taken",
    "report_skipped",
    "report_unsure",
    "list_bills",
    "create_bill_reminder",
    "report_bill_paid",
    "list_appointments",
    "request_caregiver_alert",
    "repeat",
    "help",
    "unknown",
)


@dataclass
class Intent:
    name: str
    slots: dict = field(default_factory=dict)


class IntentModel(Protocol):
    def classify(self, text: str) -> Intent: ...


_NUMBER_WORDS = {"one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7}

_PATTERNS: list[tuple[str, str]] = [
    ("repeat", r"\b(repeat|say (that|it) again|what did you say|pardon|one more time)\b"),
    ("report_unsure", r"(not sure|don'?t (know|remember)|can'?t remember|unsure) (if|whether) i (took|had)"),
    ("report_skipped", r"\bi (skipped|did(n'?t| not) take|haven'?t taken|won'?t take)\b|\bskip (it|this one)\b"),
    ("report_bill_paid", r"\b(i|we|i'?ve|i have) (already |just )?paid\b"),
    ("create_bill_reminder", r"\bremind me\b.*\b(bill|due|payment|rent|electric|water|gas|phone|internet)\b"),
    ("report_taken", r"\bi('?ve| have)? (just |already )?(took|taken|swallowed)\b|\bi('?ve| have)? (just |already )?had (it|them|my (pill|medicine|medication|tablet)s?)\b|^(took (it|them)|done|all done|i did)\b"),
    ("request_caregiver_alert", r"\b(tell|let|ask|alert|notify|message|call)\b.*\b(daughter|son|caregiver|family|daniel|helper|someone)\b|i need (some )?help with"),
    ("medication_education", r"what('?s| is| are| does)\b.*\b(for|do|used|treat)\b|tell me (more )?about|what is (this|that|my) (medicine|medication|pill)|why (do i|am i) tak"),
    ("list_appointments", r"\b(appointment|doctor|check-?up|visit|clinic)s?\b"),
    ("list_bills", r"\b(bills?|due|owe|payments?)\b"),
    ("list_medications", r"\b(my|which|what) (medicines?|medications?|pills?|prescriptions?)\b"),
    ("today_briefing", r"\b(today|this (morning|afternoon|evening)|tonight|what should i do|what'?s next|what do i (have|need)|my day|schedule|to-?do|good morning)\b"),
    ("help", r"\b(help|what can you do|how do (i|you))\b"),
]
_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _PATTERNS]


def _slots(text: str) -> dict:
    lowered = text.lower()
    slots: dict = {}
    for period in ("morning", "afternoon", "evening", "tonight"):
        if period in lowered:
            slots["period"] = {"afternoon": "midday", "tonight": "night"}.get(period, period)
            break
    lead = re.search(r"(\d+|one|a|two|three|four|five|seven) (day|week)s? (before|ahead|early)", lowered)
    if lead:
        count = int(lead.group(1)) if lead.group(1).isdigit() else _NUMBER_WORDS[lead.group(1)]
        slots["lead_days"] = count * (7 if lead.group(2) == "week" else 1)
    elif "day before" in lowered:
        slots["lead_days"] = 1
    return slots


class RuleBasedIntentModel:
    def classify(self, text: str) -> Intent:
        cleaned = " ".join(text.split())
        for name, pattern in _COMPILED:
            if pattern.search(cleaned):
                return Intent(name, _slots(cleaned))
        return Intent("unknown", _slots(cleaned))


PERIODS = ("morning", "midday", "evening", "night")


def _llm_slots(data: dict) -> dict:
    """Keep only well-formed details from the model; everything else is dropped."""
    slots: dict = {}
    if data.get("period") in PERIODS:
        slots["period"] = data["period"]
    lead = data.get("lead_days")
    if isinstance(lead, int) and not isinstance(lead, bool) and 0 <= lead <= 30:
        slots["lead_days"] = lead
    medicine = data.get("medicine")
    if isinstance(medicine, str) and 0 < len(medicine.strip()) <= 80:
        slots["medicine"] = medicine.strip().lower()
    return slots


class ConstrainedLLMIntentModel:
    """Adapter seam for an LLM classifier (see app/ai/llm.py for the Claude one).

    `complete` returns an intent name, or a dict with `intent` and optional
    `medicine` / `period` / `lead_days`. Anything outside `INTENTS` is
    discarded and the rule-based model is used instead, so a model failure or
    a prompt injection can at worst pick the wrong allowed intent. Details are
    validated here, never trusted as-is.
    """

    def __init__(self, complete, fallback: IntentModel | None = None):
        self._complete = complete
        self._fallback = fallback or RuleBasedIntentModel()

    def classify(self, text: str) -> Intent:
        try:
            result = self._complete(text, INTENTS)
        except Exception:
            return self._fallback.classify(text)
        if isinstance(result, dict):
            name, details = str(result.get("intent", "")).strip(), result
        else:
            name, details = str(result).strip(), {}
        if name not in INTENTS:
            return self._fallback.classify(text)
        return Intent(name, {**_slots(text), **_llm_slots(details)})
