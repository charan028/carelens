"""Deterministic dose scheduling.

Dose times come only from `dose_schedules`, which exist only for prescriptions
a verifier has confirmed. Nothing in this module reads free text or model
output. Local wall-clock times are converted with the IANA timezone database:

* A time that does not exist on a spring-forward day (02:30 in the gap) moves
  forward to the first valid instant, so the dose is still scheduled once.
* A time that occurs twice on a fall-back day uses the first occurrence, so
  the dose is scheduled once, not twice.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ... import clock
from ...models import (
    DoseEvent,
    DoseSchedule,
    DoseStatus,
    MedicationCatalog,
    Prescription,
    PrescriptionStatus,
    User,
)
from .. import audit
from .state_machine import apply_system


def local_to_utc(day: date, local_time: str, tz_name: str) -> datetime:
    hour, minute = (int(part) for part in local_time.split(":"))
    tz = ZoneInfo(tz_name)
    wall = datetime.combine(day, time(hour, minute))
    # fold=0 picks the first occurrence of an ambiguous time and, for a
    # nonexistent time, the pre-transition offset, which lands after the gap.
    return wall.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)


def local_day(moment: datetime, tz_name: str) -> date:
    return moment.astimezone(ZoneInfo(tz_name)).date()


def day_bounds_utc(day: date, tz_name: str) -> tuple[datetime, datetime]:
    return local_to_utc(day, "00:00", tz_name), local_to_utc(day + timedelta(days=1), "00:00", tz_name)


def _applies(schedule: DoseSchedule, day: date) -> bool:
    rx = schedule.prescription
    if rx.status != PrescriptionStatus.ACTIVE or rx.verified_at is None:
        return False
    if day < max(schedule.effective_from, rx.valid_from):
        return False
    for end in (schedule.effective_until, rx.valid_until):
        if end is not None and day > end:
            return False
    if schedule.days_of_week is not None and day.weekday() not in schedule.days_of_week:
        return False
    return True


def materialize(db: Session, user: User, start: date, end: date) -> list[DoseEvent]:
    """Create any missing dose events for `user` between two local dates.

    Safe to call repeatedly: (schedule, instant) is unique, so restarts and
    overlapping runs never duplicate a dose.
    """
    schedules = db.scalars(
        select(DoseSchedule).join(Prescription).where(Prescription.user_id == user.id)
    ).all()
    created: list[DoseEvent] = []
    day = start
    while day <= end:
        for schedule in schedules:
            if not _applies(schedule, day):
                continue
            at = local_to_utc(day, schedule.local_time, schedule.timezone)
            exists = db.scalar(
                select(DoseEvent.id).where(
                    DoseEvent.schedule_id == schedule.id, DoseEvent.scheduled_at == at
                )
            )
            if exists:
                continue
            event = DoseEvent(
                schedule_id=schedule.id, user_id=user.id, scheduled_at=at, reminder_at=at
            )
            try:
                # Savepoint: if another worker created the same dose a moment
                # ago, the unique constraint rejects ours and we move on.
                with db.begin_nested():
                    db.add(event)
            except IntegrityError:
                continue
            created.append(event)
        day += timedelta(days=1)
    db.flush()
    return created


def verify_prescription(
    db: Session, prescription: Prescription, verifier: User, times: list[str] | None = None
) -> Prescription:
    """Confirm provenance and activate the schedule written on the record.

    `times` lets the verifier correct the recorded times; it is never filled
    by the assistant.
    """
    if prescription.status != PrescriptionStatus.PENDING:
        raise ValueError("Only a prescription that is waiting for verification can be verified.")
    owner = db.get(User, prescription.user_id)
    chosen = times or list(prescription.record_times)
    if not chosen:
        raise ValueError("The record has no dose times to verify.")
    for value in chosen:
        hour, _, minute = value.partition(":")
        if not (hour.isdigit() and minute.isdigit() and 0 <= int(hour) < 24 and 0 <= int(minute) < 60):
            raise ValueError(f"'{value}' is not a valid HH:MM time.")
    now = clock.now()
    prescription.status = PrescriptionStatus.ACTIVE
    prescription.verified_by = verifier.id
    prescription.verified_at = now
    prescription.record_times = chosen
    today = local_day(now, owner.timezone)
    for value in chosen:
        db.add(
            DoseSchedule(
                prescription_id=prescription.id,
                local_time=f"{int(value[:2]):02d}:{int(value[3:]):02d}",
                timezone=owner.timezone,
                effective_from=max(today, prescription.valid_from),
                effective_until=prescription.valid_until,
            )
        )
    audit.record(
        db,
        actor_id=verifier.id,
        subject_user_id=owner.id,
        action="prescription.verify",
        resource_type="prescription",
        resource_id=prescription.id,
    )
    db.flush()
    db.refresh(prescription)
    return prescription


def discontinue_prescription(db: Session, prescription: Prescription, verifier: User) -> None:
    prescription.status = PrescriptionStatus.DISCONTINUED
    pending = db.scalars(
        select(DoseEvent)
        .join(DoseSchedule)
        .where(
            DoseSchedule.prescription_id == prescription.id,
            DoseEvent.status.in_(DoseStatus.AWAITING),
        )
    ).all()
    for event in pending:
        apply_system(event, DoseStatus.DISCONTINUED)
    audit.record(
        db,
        actor_id=verifier.id,
        subject_user_id=prescription.user_id,
        action="prescription.discontinue",
        resource_type="prescription",
        resource_id=prescription.id,
    )


def describe(event: DoseEvent) -> MedicationCatalog:
    return event.schedule.prescription.catalog
