"""Reminder engine: one idempotent `tick` that moves time-driven state.

It runs on a timer in the API process and can be called at any moment, any
number of times, with the same result. It has no in-memory state: everything
it needs is in the database, so reminders survive restarts. It never calls a
language model.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import (
    Appointment,
    Bill,
    BillStatus,
    DoseEvent,
    DoseStatus,
    Role,
    User,
)
from .. import billing, caregiver
from ..medication import scheduler
from ..medication.state_machine import apply_system
from . import outbox

REMINDER_REPEAT = timedelta(minutes=15)   # gap between repeat reminders
MAX_REMINDERS = 3
OVERDUE_AFTER = timedelta(minutes=60)     # confirmation is late
WINDOW_CLOSES_AFTER = timedelta(hours=4)  # stop asking; record as unknown
BILL_REMINDER_LOCAL_TIME = "09:00"

NOT_AN_EMERGENCY = (
    "This is a routine check-in notice, not an emergency alert, and it does not "
    "mean a dose was missed."
)


def fmt_time(moment: datetime, tz_name: str) -> str:
    local = moment.astimezone(ZoneInfo(tz_name))
    return local.strftime("%I:%M %p").lstrip("0")


def fmt_date(day) -> str:
    return f"{day.strftime('%A, %B')} {day.day}"


def money(cents: int | None) -> str:
    return "" if cents is None else f"${cents / 100:,.2f}"


def tick(db: Session, now: datetime | None = None) -> dict:
    now = now or clock.now()
    stats = {"doses_created": 0, "reminders": 0, "caregiver_alerts": 0, "delivered": 0}
    for user in db.scalars(select(User).where(User.role == Role.SENIOR)).all():
        today = scheduler.local_day(now, user.timezone)
        stats["doses_created"] += len(
            scheduler.materialize(db, user, today, today + timedelta(days=1))
        )
        _advance_doses(db, user, now, stats)
        _advance_bills(db, user, now, today, stats)
        _appointment_reminders(db, user, now, stats)
    stats["delivered"] = outbox.dispatch(db, now=now)
    db.commit()
    return stats


def _advance_doses(db: Session, user: User, now: datetime, stats: dict) -> None:
    events = db.scalars(
        select(DoseEvent).where(
            DoseEvent.user_id == user.id,
            DoseEvent.status.in_(DoseStatus.AWAITING),
            DoseEvent.scheduled_at <= now,
        )
    ).all()
    for event in events:
        drug = scheduler.describe(event)
        age = now - event.scheduled_at
        when = fmt_time(event.scheduled_at, user.timezone)

        # Reminders owed so far: one at the scheduled time, then repeats.
        owed = min(MAX_REMINDERS, 1 + int(age / REMINDER_REPEAT))
        if age >= WINDOW_CLOSES_AFTER:
            owed = event.reminders_sent  # too late to nag; just close the window
        while event.reminders_sent < owed:
            number = event.reminders_sent + 1
            queued = outbox.enqueue(
                db,
                key=f"dose:{event.id}:reminder:{number}",
                kind="dose_reminder",
                target_user_id=user.id,
                subject_user_id=user.id,
                title=f"{when} medicine",
                body=f"It is time for your {drug.generic_name} {drug.strength}. "
                "Tell me when you have taken it.",
                due_at=now,
                resource_type="dose_event",
                resource_id=event.id,
            )
            event.reminders_sent = number
            if queued:
                stats["reminders"] += 1

        if event.status == DoseStatus.SCHEDULED:
            apply_system(event, DoseStatus.REMINDED)
        if event.status == DoseStatus.REMINDED and age >= OVERDUE_AFTER:
            apply_system(event, DoseStatus.OVERDUE)

        if (
            user.escalation_enabled
            and event.reminders_sent >= max(1, user.escalation_after_reminders)
            and age >= REMINDER_REPEAT * max(1, user.escalation_after_reminders)
        ):
            for grant in caregiver.alert_recipients(db, user.id, "medications"):
                queued = outbox.enqueue(
                    db,
                    key=f"dose:{event.id}:caregiver:{grant.caregiver_id}",
                    kind="caregiver_alert",
                    target_user_id=grant.caregiver_id,
                    subject_user_id=user.id,
                    title=f"{user.display_name} has not confirmed a {when} medicine",
                    body=f"{event.reminders_sent} reminders were sent with no reply. "
                    + NOT_AN_EMERGENCY,
                    due_at=now,
                    resource_type="dose_event",
                    resource_id=event.id,
                )
                if queued:
                    stats["caregiver_alerts"] += 1

        if event.status == DoseStatus.OVERDUE and age >= WINDOW_CLOSES_AFTER:
            apply_system(event, DoseStatus.UNKNOWN)


def _advance_bills(db: Session, user: User, now: datetime, today, stats: dict) -> None:
    bills = db.scalars(
        select(Bill).where(Bill.owner_id == user.id, Bill.status.in_(BillStatus.OPEN))
    ).all()
    for bill in bills:
        # The calendar may move a bill between open states. It can never mark
        # a bill paid.
        calendar_status = billing.date_status(bill.due_date, today)
        if calendar_status != bill.status:
            bill.status = calendar_status
        remind_on = bill.due_date - timedelta(days=bill.reminder_lead_days)
        remind_at = scheduler.local_to_utc(remind_on, BILL_REMINDER_LOCAL_TIME, user.timezone)
        if now >= remind_at:
            amount = f" of {money(bill.amount_cents)}" if bill.amount_cents is not None else ""
            queued = outbox.enqueue(
                db,
                key=f"bill:{bill.id}:reminder",
                kind="bill_reminder",
                target_user_id=user.id,
                subject_user_id=user.id,
                title=f"{bill.payee_label} bill",
                body=f"Your {bill.payee_label} bill{amount} is due {fmt_date(bill.due_date)}. "
                "I can remind you, but I cannot pay it.",
                due_at=now,
                resource_type="bill",
                resource_id=bill.id,
            )
            if queued:
                billing.record_reminder_fired(db, bill)
                stats["reminders"] += 1


def _appointment_reminders(db: Session, user: User, now: datetime, stats: dict) -> None:
    upcoming = db.scalars(
        select(Appointment).where(Appointment.owner_id == user.id, Appointment.starts_at > now)
    ).all()
    for appointment in upcoming:
        if now >= appointment.starts_at - timedelta(minutes=appointment.reminder_lead_minutes):
            queued = outbox.enqueue(
                db,
                key=f"appt:{appointment.id}:reminder",
                kind="appointment_reminder",
                target_user_id=user.id,
                subject_user_id=user.id,
                title=appointment.title,
                body=f"{appointment.title} is at {fmt_time(appointment.starts_at, user.timezone)}.",
                due_at=now,
                resource_type="appointment",
                resource_id=appointment.id,
            )
            if queued:
                stats["reminders"] += 1
