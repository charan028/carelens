"""Bill reminders and payment-status claims.

CareLens never pays a bill and never holds payment credentials. A reminder
firing changes nothing about payment status; only an explicit claim with a
named source does, and a person's own word is kept apart from a claim backed
by a biller confirmation or statement.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import Bill, BillEvent, BillStatus as S, User
from .. import audit
from ..medication.scheduler import local_day


class InvalidBillTransition(Exception):
    pass


TRANSITIONS: dict[str, set[str]] = {
    S.UPCOMING: {S.DUE, S.OVERDUE, S.SELF_REPORTED_PAID, S.VERIFIED_PAID, S.CANCELLED},
    S.DUE: {S.OVERDUE, S.SELF_REPORTED_PAID, S.VERIFIED_PAID, S.CANCELLED},
    S.OVERDUE: {S.SELF_REPORTED_PAID, S.VERIFIED_PAID, S.CANCELLED},
    # A self-report can be confirmed or taken back.
    S.SELF_REPORTED_PAID: {S.VERIFIED_PAID, S.UPCOMING, S.DUE, S.OVERDUE},
    S.VERIFIED_PAID: set(),
    S.CANCELLED: set(),
}

# Sources that can back a `verified_paid` claim. The voice assistant is not
# one of them.
VERIFYING_SOURCES = {"biller_confirmation", "bank_statement"}
RECURRENCES = {"none", "weekly", "monthly", "yearly"}

LABELS = {
    S.UPCOMING: "Upcoming",
    S.DUE: "Due today",
    S.OVERDUE: "Past due date",
    S.SELF_REPORTED_PAID: "Self-reported paid",
    S.VERIFIED_PAID: "Payment confirmed by record",
    S.CANCELLED: "Cancelled",
}


def date_status(due: date, today: date) -> str:
    if today < due:
        return S.UPCOMING
    return S.DUE if today == due else S.OVERDUE


def next_due_date(due: date, recurrence: str, anchor_day: int) -> date | None:
    if recurrence == "weekly":
        return due + timedelta(days=7)
    if recurrence == "monthly":
        year, month = (due.year + 1, 1) if due.month == 12 else (due.year, due.month + 1)
        return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))
    if recurrence == "yearly":
        year = due.year + 1
        return date(year, due.month, min(anchor_day, calendar.monthrange(year, due.month)[1]))
    return None


def _event(db: Session, bill: Bill, kind: str, source: str, actor_id: str | None, detail: str = ""):
    db.add(
        BillEvent(
            bill_id=bill.id, kind=kind, source=source, actor_id=actor_id, at=clock.now(), detail=detail
        )
    )


def create_bill(
    db: Session,
    owner: User,
    *,
    payee_label: str,
    due_date: date,
    amount_cents: int | None = None,
    recurrence: str = "none",
    reminder_lead_days: int = 2,
    private: bool = False,
    today: date | None = None,
) -> Bill:
    if recurrence not in RECURRENCES:
        raise ValueError("Recurrence must be none, weekly, monthly or yearly.")
    bill = Bill(
        owner_id=owner.id,
        payee_label=payee_label.strip(),
        amount_cents=amount_cents,
        due_date=due_date,
        anchor_day=due_date.day,
        recurrence=recurrence,
        reminder_lead_days=reminder_lead_days,
        private=private,
        status=date_status(due_date, today or local_day(clock.now(), owner.timezone)),
    )
    db.add(bill)
    db.flush()
    _event(db, bill, "created", "user_entry", owner.id)
    audit.record(
        db,
        actor_id=owner.id,
        subject_user_id=owner.id,
        action="bill.create",
        resource_type="bill",
        resource_id=bill.id,
    )
    return bill


def set_status(
    db: Session, bill: Bill, actor: User, *, status: str, source: str, note: str = ""
) -> Bill:
    """Record a payment claim (or a cancellation) with its provenance."""
    if status not in TRANSITIONS.get(bill.status, set()):
        raise InvalidBillTransition(f"A bill that is {bill.status} cannot become {status}.")
    if status == S.VERIFIED_PAID and source not in VERIFYING_SOURCES:
        raise InvalidBillTransition(
            "Confirmed payment needs a biller confirmation or a statement as its source."
        )
    if status in (S.UPCOMING, S.DUE, S.OVERDUE):
        # Taking back a self-report: the calendar decides the open state.
        status = date_status(bill.due_date, local_day(clock.now(), actor.timezone))
    bill.status = status
    _event(db, bill, f"status:{status}", source, actor.id, note[:300])
    audit.record(
        db,
        actor_id=actor.id,
        subject_user_id=bill.owner_id,
        action=f"bill.status.{status}",
        resource_type="bill",
        resource_id=bill.id,
        detail={"source": source},
    )
    if status in (S.SELF_REPORTED_PAID, S.VERIFIED_PAID):
        _ensure_next_occurrence(db, bill)
    db.flush()
    return bill


def _ensure_next_occurrence(db: Session, bill: Bill) -> Bill | None:
    following = next_due_date(bill.due_date, bill.recurrence, bill.anchor_day)
    if following is None:
        return None
    existing = db.scalar(
        select(Bill).where(Bill.series_id == bill.series_id, Bill.due_date == following)
    )
    if existing:
        return existing
    upcoming = Bill(
        series_id=bill.series_id,
        owner_id=bill.owner_id,
        payee_label=bill.payee_label,
        amount_cents=None,  # next amount is not known until the next statement
        due_date=following,
        anchor_day=bill.anchor_day,
        recurrence=bill.recurrence,
        reminder_lead_days=bill.reminder_lead_days,
        private=bill.private,
        status=S.UPCOMING,
    )
    db.add(upcoming)
    db.flush()
    _event(db, upcoming, "created", "recurrence", None)
    return upcoming


def record_reminder_fired(db: Session, bill: Bill) -> None:
    """Log that a reminder went out. Payment status is not touched."""
    _event(db, bill, "reminder_fired", "reminder_engine", None)
