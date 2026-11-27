"""Read models: the day's tasks for a senior, and the scoped caregiver view.

Both views are built from the same serializers, so a caregiver can never see
a field the senior view does not have, and scope filtering happens in exactly
one function (`caregiver_view`).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import (
    Appointment,
    Bill,
    BillStatus,
    CaregiverGrant,
    DoseEvent,
    DoseStatus,
    NotificationOutbox,
    Prescription,
    PrescriptionStatus,
    User,
)
from . import billing
from .medication import scheduler
from .medication.state_machine import LABELS as DOSE_LABELS
from .notifications.engine import fmt_date, fmt_time, money

SYNTHETIC_NOTICE = "Synthetic demo data. No real person, prescription or bill."
BILL_HORIZON_DAYS = 14
APPOINTMENT_HORIZON_DAYS = 7


def period_of(hour: int) -> str:
    if hour < 11:
        return "morning"
    if hour < 15:
        return "midday"
    if hour < 20:
        return "evening"
    return "night"


def dose_dict(event: DoseEvent, tz_name: str) -> dict:
    rx = event.schedule.prescription
    drug = rx.catalog
    local = event.scheduled_at.astimezone(ZoneInfo(tz_name))
    return {
        "dose_event_id": event.id,
        "prescription_id": rx.id,
        "rxcui": drug.rxcui,
        "name": drug.generic_name,
        "strength": drug.strength,
        "dose_form": drug.dose_form,
        "directions": rx.verbatim_directions,
        "directions_source": rx.source,
        "scheduled_at": event.scheduled_at.isoformat(),
        "local_time": fmt_time(event.scheduled_at, tz_name),
        "period": period_of(local.hour),
        "status": event.status,
        "status_label": DOSE_LABELS[event.status],
        "origin": event.origin,
        "self_reported": event.origin == "self_report",
        "independently_verified": False,  # no ingestion evidence exists in the MVP
        "response_at": event.response_at.isoformat() if event.response_at else None,
        "reminders_sent": event.reminders_sent,
    }


def bill_dict(bill: Bill, today) -> dict:
    return {
        "id": bill.id,
        "payee_label": bill.payee_label,
        "amount_cents": bill.amount_cents,
        "amount": money(bill.amount_cents),
        "due_date": bill.due_date.isoformat(),
        "due_label": fmt_date(bill.due_date),
        "days_until_due": (bill.due_date - today).days,
        "recurrence": bill.recurrence,
        "reminder_lead_days": bill.reminder_lead_days,
        "status": bill.status,
        "status_label": billing.LABELS[bill.status],
        "self_reported": bill.status == BillStatus.SELF_REPORTED_PAID,
        "independently_verified": bill.status == BillStatus.VERIFIED_PAID,
        "private": bill.private,
    }


def appointment_dict(appointment: Appointment, tz_name: str) -> dict:
    local = appointment.starts_at.astimezone(ZoneInfo(tz_name))
    return {
        "id": appointment.id,
        "title": appointment.title,
        "kind": appointment.kind,
        "location": appointment.location,
        "starts_at": appointment.starts_at.isoformat(),
        "date_label": fmt_date(local.date()),
        "local_time": fmt_time(appointment.starts_at, tz_name),
    }


def notification_dict(note: NotificationOutbox) -> dict:
    return {
        "id": note.id,
        "kind": note.kind,
        "title": note.title,
        "body": note.body,
        "resource_type": note.resource_type,
        "resource_id": note.resource_id,
        "delivered_at": note.delivered_at.isoformat() if note.delivered_at else None,
    }


def doses_for_day(db: Session, user: User, now: datetime) -> list[DoseEvent]:
    today = scheduler.local_day(now, user.timezone)
    scheduler.materialize(db, user, today, today)
    start, end = scheduler.day_bounds_utc(today, user.timezone)
    return list(
        db.scalars(
            select(DoseEvent)
            .where(
                DoseEvent.user_id == user.id,
                DoseEvent.scheduled_at >= start,
                DoseEvent.scheduled_at < end,
                DoseEvent.status != DoseStatus.DISCONTINUED,
            )
            .order_by(DoseEvent.scheduled_at)
        )
    )


def open_bills(db: Session, user: User, today, horizon_days: int = BILL_HORIZON_DAYS) -> list[Bill]:
    return list(
        db.scalars(
            select(Bill)
            .where(
                Bill.owner_id == user.id,
                Bill.status != BillStatus.CANCELLED,
                Bill.due_date <= today + timedelta(days=horizon_days),
                Bill.due_date >= today - timedelta(days=45),
            )
            .order_by(Bill.due_date)
        )
    )


def upcoming_appointments(db: Session, user: User, now: datetime) -> list[Appointment]:
    return list(
        db.scalars(
            select(Appointment)
            .where(
                Appointment.owner_id == user.id,
                Appointment.starts_at >= now - timedelta(hours=1),
                Appointment.starts_at <= now + timedelta(days=APPOINTMENT_HORIZON_DAYS),
            )
            .order_by(Appointment.starts_at)
        )
    )


def build_today(db: Session, user: User, now: datetime | None = None) -> dict:
    now = now or clock.now()
    today = scheduler.local_day(now, user.timezone)
    doses = [dose_dict(event, user.timezone) for event in doses_for_day(db, user, now)]
    bills = [bill_dict(bill, today) for bill in open_bills(db, user, today)]
    appointments = [appointment_dict(a, user.timezone) for a in upcoming_appointments(db, user, now)]
    pending = db.scalars(
        select(Prescription).where(
            Prescription.user_id == user.id, Prescription.status == PrescriptionStatus.PENDING
        )
    ).all()
    db.commit()
    return {
        "synthetic_notice": SYNTHETIC_NOTICE if user.is_synthetic else None,
        "user": {"id": user.id, "display_name": user.display_name, "timezone": user.timezone},
        "now": now.isoformat(),
        "local_now": fmt_time(now, user.timezone),
        "date": today.isoformat(),
        "date_label": fmt_date(today),
        "clock_simulated": clock.is_simulated(),
        "medications": doses,
        "bills": bills,
        "appointments": appointments,
        "awaiting_verification": [
            {
                "prescription_id": rx.id,
                "name": rx.catalog.generic_name,
                "strength": rx.catalog.strength,
                "note": "Waiting for a pharmacist to verify. No reminders are scheduled yet.",
            }
            for rx in pending
        ],
    }


def caregiver_view(db: Session, grant: CaregiverGrant, now: datetime | None = None) -> dict:
    """Everything one caregiver may see about one senior, and nothing else."""
    now = now or clock.now()
    senior = db.get(User, grant.senior_id)
    today = scheduler.local_day(now, senior.timezone)
    view: dict = {
        "senior": {"id": senior.id, "display_name": senior.display_name},
        "scopes": list(grant.scopes),
        "alerts_enabled": grant.alerts_enabled,
        "granted_at": grant.granted_at.isoformat(),
        "date_label": fmt_date(today),
        "local_now": fmt_time(now, senior.timezone),
        "synthetic_notice": SYNTHETIC_NOTICE if senior.is_synthetic else None,
    }
    if "medications" in grant.scopes:
        view["medications"] = [
            dose_dict(event, senior.timezone) for event in doses_for_day(db, senior, now)
        ]
    if "bills" in grant.scopes:
        view["bills"] = [
            bill_dict(bill, today) for bill in open_bills(db, senior, today) if not bill.private
        ]
    if "appointments" in grant.scopes:
        view["appointments"] = [
            appointment_dict(a, senior.timezone) for a in upcoming_appointments(db, senior, now)
        ]
    alerts = db.scalars(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.target_user_id == grant.caregiver_id,
            NotificationOutbox.subject_user_id == senior.id,
            NotificationOutbox.status == "delivered",
            NotificationOutbox.delivered_at >= grant.granted_at,
        )
        .order_by(NotificationOutbox.delivered_at.desc())
        .limit(10)
    ).all()
    view["alerts"] = [notification_dict(note) for note in alerts]
    db.commit()
    return view
