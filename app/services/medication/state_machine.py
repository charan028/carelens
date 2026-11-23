"""Dose-event state machine.

There is deliberately no "taken" or "verified taken" state. The strongest
claim the system can make is `self_reported_taken`: the person said so.
Silence moves an event to `overdue` (confirmation is late) and then `unknown`
(the window closed with no answer). Neither means a dose was missed.
"""
from __future__ import annotations

from datetime import datetime

from ...models import DoseEvent, DoseStatus as S


class InvalidTransition(Exception):
    pass


# What the reminder engine may do on its own.
SYSTEM_TRANSITIONS: dict[str, set[str]] = {
    S.SCHEDULED: {S.REMINDED, S.DISCONTINUED},
    S.REMINDED: {S.OVERDUE, S.DISCONTINUED},
    S.OVERDUE: {S.UNKNOWN, S.DISCONTINUED},
    S.UNKNOWN: set(),
    S.SELF_REPORTED_TAKEN: set(),
    S.SKIPPED: set(),
    S.DISCONTINUED: set(),
}

# What the person's own words may do. Corrections are allowed; a
# discontinued dose cannot be reported on.
REPORT_TO_STATUS = {"taken": S.SELF_REPORTED_TAKEN, "skipped": S.SKIPPED, "unsure": S.UNKNOWN}
REPORTABLE_FROM = {S.SCHEDULED, S.REMINDED, S.OVERDUE, S.UNKNOWN, S.SELF_REPORTED_TAKEN, S.SKIPPED}


def apply_system(event: DoseEvent, target: str) -> DoseEvent:
    if target not in SYSTEM_TRANSITIONS.get(event.status, set()):
        raise InvalidTransition(f"system cannot move a dose from {event.status} to {target}")
    event.status = target
    event.origin = "system"
    return event


def apply_self_report(event: DoseEvent, report: str, at: datetime) -> DoseEvent:
    if report not in REPORT_TO_STATUS:
        raise InvalidTransition(f"unknown report '{report}'")
    if event.status not in REPORTABLE_FROM:
        raise InvalidTransition(f"a {event.status} dose cannot be reported on")
    event.status = REPORT_TO_STATUS[report]
    event.origin = "self_report"
    event.response_at = at
    return event


LABELS = {
    S.SCHEDULED: "Coming up",
    S.REMINDED: "Due now, not confirmed",
    S.OVERDUE: "Not confirmed yet",
    S.UNKNOWN: "Unconfirmed",
    S.SELF_REPORTED_TAKEN: "Self-reported taken",
    S.SKIPPED: "Self-reported skipped",
    S.DISCONTINUED: "Discontinued",
}
