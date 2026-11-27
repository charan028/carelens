"""Endpoints for the older adult's own records: today, doses, bills, sharing."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...ai import assistant, tools
from ...auth import require_role
from ...db import get_db
from ...models import (
    Appointment,
    AuditEvent,
    Bill,
    DoseEvent,
    NotificationOutbox,
    Prescription,
    Role,
    User,
)
from ...schemas import (
    AppointmentCreate,
    AssistantMessage,
    BillCreate,
    BillStatusUpdate,
    ConsentChange,
    EscalationSettings,
    SelfReport,
)
from ...services import audit, billing, caregiver, today
from ...services.medication import scheduler
from ...services.medication.state_machine import InvalidTransition
from ..deps import idempotent, rate_limit

router = APIRouter(prefix="/v1", tags=["senior"])
senior_only = require_role(Role.SENIOR)


@router.get("/me/today")
def me_today(user: User = Depends(senior_only), db: Session = Depends(get_db)):
    return today.build_today(db, user)


@router.get("/medications")
def medications(user: User = Depends(senior_only), db: Session = Depends(get_db)):
    pending = db.scalars(
        select(Prescription).where(
            Prescription.user_id == user.id, Prescription.status == "pending_verification"
        )
    ).all()
    return {
        "verified": tools.get_verified_medications(db, user),
        "awaiting_verification": [
            {"prescription_id": rx.id, "name": rx.catalog.generic_name, "strength": rx.catalog.strength}
            for rx in pending
        ],
    }


@router.post("/dose-events/{dose_event_id}/self-report")
def self_report(
    dose_event_id: str,
    body: SelfReport,
    user: User = Depends(senior_only),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    event = db.get(DoseEvent, dose_event_id)
    # Same answer for "does not exist" and "not yours", so ids cannot be probed.
    if event is None or event.user_id != user.id:
        raise HTTPException(status_code=404, detail="That reminder was not found.")

    def action() -> dict:
        try:
            return tools.record_dose_self_report(db, user, dose_event_id, body.status)
        except InvalidTransition as error:
            raise HTTPException(status_code=409, detail=str(error))

    return idempotent(db, user, f"dose:{dose_event_id}", idempotency_key, action)


@router.get("/bills")
def bills(user: User = Depends(senior_only), db: Session = Depends(get_db)):
    return {"bills": tools.get_bill_due_dates(db, user)}


@router.post("/bills", status_code=201)
def create_bill(
    body: BillCreate,
    user: User = Depends(senior_only),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    def action() -> dict:
        bill = billing.create_bill(db, user, **body.model_dump())
        db.commit()
        return today.bill_dict(bill, scheduler.local_day(clock.now(), user.timezone))

    return idempotent(db, user, "bill:create", idempotency_key, action)


@router.post("/bills/{bill_id}/status")
def bill_status(
    bill_id: str,
    body: BillStatusUpdate,
    user: User = Depends(senior_only),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    bill = db.get(Bill, bill_id)
    if bill is None or bill.owner_id != user.id:
        raise HTTPException(status_code=404, detail="That bill was not found.")

    def action() -> dict:
        try:
            billing.set_status(db, bill, user, status=body.status, source=body.source, note=body.note)
        except billing.InvalidBillTransition as error:
            raise HTTPException(status_code=409, detail=str(error))
        db.commit()
        return today.bill_dict(bill, scheduler.local_day(clock.now(), user.timezone))

    return idempotent(db, user, f"bill:{bill_id}", idempotency_key, action)


@router.post("/appointments", status_code=201)
def create_appointment(
    body: AppointmentCreate,
    user: User = Depends(senior_only),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None),
):
    """Manual appointment entry (blueprint 3C). The reminder engine picks it up."""
    starts_at = scheduler.local_to_utc(body.starts_on, body.local_time, user.timezone)
    if starts_at <= clock.now():
        raise HTTPException(status_code=422, detail="That date and time have already passed.")

    def action() -> dict:
        appointment = Appointment(
            owner_id=user.id,
            title=body.title,
            location=body.location,
            starts_at=starts_at,
            reminder_lead_minutes=body.reminder_lead_minutes,
        )
        db.add(appointment)
        db.flush()
        audit.record(
            db,
            actor_id=user.id,
            subject_user_id=user.id,
            action="appointment.create",
            resource_type="appointment",
            resource_id=appointment.id,
        )
        db.commit()
        return today.appointment_dict(appointment, user.timezone)

    return idempotent(db, user, "appointment:create", idempotency_key, action)


@router.get("/notifications")
def notifications(user: User = Depends(require_role(Role.SENIOR, Role.CAREGIVER)), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(NotificationOutbox)
        .where(NotificationOutbox.target_user_id == user.id, NotificationOutbox.status == "delivered")
        .order_by(NotificationOutbox.delivered_at.desc())
        .limit(20)
    ).all()
    return {"notifications": [today.notification_dict(note) for note in rows]}


@router.get("/consents")
def consents(user: User = Depends(senior_only), db: Session = Depends(get_db)):
    grants = {g.caregiver_id: g for g in caregiver.grants_for_senior(db, user.id)}
    people = db.scalars(select(User).where(User.role == Role.CAREGIVER)).all()
    return {
        "caregivers": [
            {
                "caregiver_id": person.id,
                "display_name": person.display_name,
                "scopes": grants[person.id].scopes if person.id in grants else [],
                "alerts_enabled": grants[person.id].alerts_enabled if person.id in grants else False,
                "granted_at": grants[person.id].granted_at.isoformat() if person.id in grants else None,
            }
            for person in people
        ],
        "escalation_enabled": user.escalation_enabled,
        "escalation_after_reminders": user.escalation_after_reminders,
    }


@router.post("/consents")
def change_consent(body: ConsentChange, user: User = Depends(senior_only), db: Session = Depends(get_db)):
    person = db.get(User, body.caregiver_id)
    if person is None or person.role != Role.CAREGIVER:
        raise HTTPException(status_code=404, detail="That caregiver account was not found.")
    if body.action == "revoke" or not body.scopes:
        caregiver.revoke(db, user, person.id)
    else:
        try:
            caregiver.grant(db, user, person, list(body.scopes), body.alerts_enabled)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error))
    db.commit()
    return consents(user, db)


@router.post("/me/escalation")
def escalation(body: EscalationSettings, user: User = Depends(senior_only), db: Session = Depends(get_db)):
    user = db.get(User, user.id)
    user.escalation_enabled = body.escalation_enabled
    user.escalation_after_reminders = body.escalation_after_reminders
    audit.record(
        db, actor_id=user.id, subject_user_id=user.id, action="escalation.update",
        resource_type="user", resource_id=user.id,
        detail={"enabled": body.escalation_enabled, "after": body.escalation_after_reminders},
    )
    db.commit()
    return consents(user, db)


@router.get("/audit")
def audit_trail(user: User = Depends(senior_only), db: Session = Depends(get_db)):
    """Everything anyone did to this person's records, newest first."""
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.subject_user_id == user.id)
        .order_by(AuditEvent.at.desc())
        .limit(40)
    ).all()
    names = {u.id: u.display_name for u in db.scalars(select(User)).all()}
    return {
        "events": [
            {
                "at": row.at.isoformat(),
                "actor": names.get(row.actor_id, row.actor_id),
                "action": row.action,
                "resource_type": row.resource_type,
                "outcome": row.outcome,
                "detail": row.detail,
            }
            for row in rows
        ]
    }


@router.post("/assistant/message")
def assistant_message(body: AssistantMessage, user: User = Depends(senior_only), db: Session = Depends(get_db)):
    rate_limit(user)
    reply = assistant.handle(db, user, body.text, body.context)
    reply["channel"] = body.channel
    return reply
