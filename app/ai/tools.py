"""The only functions the assistant can call (blueprint section 9).

`call` is the single gate. A name that is not registered raises
`ToolNotAllowed`, and the names the blueprint forbids are listed explicitly so
a test can prove they stay unreachable. Each tool takes the authenticated user
from the request, never from the utterance, so the assistant cannot be talked
into acting on someone else's records.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Bill, BillStatus, DoseEvent, Prescription, PrescriptionStatus, User
from ..services import audit, billing, caregiver, today
from ..services.medication import education, scheduler
from ..services.medication.state_machine import apply_self_report
from ..services.notifications import outbox
from ..services.notifications.engine import NOT_AN_EMERGENCY


class ToolNotAllowed(Exception):
    pass


DISALLOWED = frozenset(
    {
        "change_dosage_without_verifier",
        "recommend_extra_dose",
        "assert_drug_pair_is_safe",
        "autonomously_pay_bill",
        "mark_medication_swallowed_without_evidence",
    }
)


def get_today_tasks(db: Session, user: User) -> dict:
    return today.build_today(db, user)


def get_verified_medications(db: Session, user: User) -> list[dict]:
    rows = db.scalars(
        select(Prescription).where(
            Prescription.user_id == user.id, Prescription.status == PrescriptionStatus.ACTIVE
        )
    ).all()
    return [
        {
            "prescription_id": rx.id,
            "rxcui": rx.rxcui,
            "name": rx.catalog.generic_name,
            "brand_names": rx.catalog.brand_names,
            "strength": rx.catalog.strength,
            "dose_form": rx.catalog.dose_form,
            "directions": rx.verbatim_directions,
            "times": sorted(schedule.local_time for schedule in rx.schedules),
        }
        for rx in rows
    ]


def get_drug_education(db: Session, user: User, rxcui: str) -> dict | None:
    return education.get_education(db, rxcui)


def get_bill_due_dates(db: Session, user: User) -> list[dict]:
    day = scheduler.local_day(clock.now(), user.timezone)
    return [today.bill_dict(bill, day) for bill in today.open_bills(db, user, day, horizon_days=45)]


def record_dose_self_report(db: Session, user: User, dose_event_id: str, status: str) -> dict:
    event = db.get(DoseEvent, dose_event_id)
    if event is None or event.user_id != user.id:
        raise LookupError("dose event not found")
    apply_self_report(event, status, clock.now())
    audit.record(
        db,
        actor_id=user.id,
        subject_user_id=user.id,
        action=f"dose.self_report.{status}",
        resource_type="dose_event",
        resource_id=event.id,
    )
    db.commit()
    return today.dose_dict(event, user.timezone)


def create_bill_reminder(db: Session, user: User, bill_id: str, reminder_at: datetime) -> dict:
    bill = db.get(Bill, bill_id)
    if bill is None or bill.owner_id != user.id:
        raise LookupError("bill not found")
    note = outbox.enqueue(
        db,
        key=f"bill:{bill.id}:extra:{reminder_at.date().isoformat()}",
        kind="bill_reminder",
        target_user_id=user.id,
        subject_user_id=user.id,
        title=f"{bill.payee_label} bill",
        body=f"You asked me to remind you: your {bill.payee_label} bill is due "
        f"{today.fmt_date(bill.due_date)}. I cannot pay it for you.",
        due_at=reminder_at,
        resource_type="bill",
        resource_id=bill.id,
    )
    audit.record(
        db,
        actor_id=user.id,
        subject_user_id=user.id,
        action="bill.reminder.create",
        resource_type="bill",
        resource_id=bill.id,
    )
    db.commit()
    return {"created": note is not None, "reminder_at": reminder_at.isoformat(), "bill_id": bill.id}


def record_bill_self_report(db: Session, user: User, bill_id: str) -> dict:
    """Record "I paid it". The only status this tool can set is self-reported."""
    bill = db.get(Bill, bill_id)
    if bill is None or bill.owner_id != user.id:
        raise LookupError("bill not found")
    billing.set_status(
        db, bill, user, status=BillStatus.SELF_REPORTED_PAID, source="voice_self_report"
    )
    db.commit()
    return today.bill_dict(bill, scheduler.local_day(clock.now(), user.timezone))


def request_caregiver_alert(db: Session, user: User, task_id: str, reason: str) -> dict:
    """Ask approved caregivers to check in. Only people the user allowed are told."""
    scope = "bills" if task_id.startswith("bill") else "medications"
    recipients = caregiver.alert_recipients(db, user.id, scope)
    now = clock.now()
    for grant in recipients:
        outbox.enqueue(
            db,
            key=f"help:{user.id}:{grant.caregiver_id}:{now.strftime('%Y%m%d%H%M')}",
            kind="caregiver_alert",
            target_user_id=grant.caregiver_id,
            subject_user_id=user.id,
            title=f"{user.display_name} asked for a check-in",
            body=f"Reason given: {reason}. {NOT_AN_EMERGENCY}",
            due_at=now,
            resource_type=scope,
            resource_id=task_id,
        )
    audit.record(
        db,
        actor_id=user.id,
        subject_user_id=user.id,
        action="caregiver.alert.request",
        resource_type=scope,
        resource_id=task_id,
        detail={"recipients": len(recipients)},
    )
    outbox.dispatch(db, now=now)
    db.commit()
    names = [db.get(User, grant.caregiver_id).display_name for grant in recipients]
    return {"notified": names}


REGISTRY: dict[str, Callable] = {
    "get_today_tasks": get_today_tasks,
    "get_verified_medications": get_verified_medications,
    "get_drug_education": get_drug_education,
    "get_bill_due_dates": get_bill_due_dates,
    "record_dose_self_report": record_dose_self_report,
    "create_bill_reminder": create_bill_reminder,
    "record_bill_self_report": record_bill_self_report,
    "request_caregiver_alert": request_caregiver_alert,
}


def call(name: str, db: Session, user: User, **arguments):
    if name in DISALLOWED or name not in REGISTRY:
        audit.record(
            db,
            actor_id=user.id,
            subject_user_id=user.id,
            action="assistant.tool.denied",
            resource_type="tool",
            resource_id=name[:60],
            outcome="denied",
        )
        db.commit()
        raise ToolNotAllowed(name)
    return REGISTRY[name](db, user, **arguments)
