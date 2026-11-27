"""Acceptance: schedules come only from verified structured records; states
never infer ingestion; reminders survive restarts and never duplicate."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app import clock, db as database, seed
from app.db import Base, SessionLocal
from app.models import DoseEvent, DoseSchedule, DoseStatus, NotificationOutbox, Prescription, User
from app.services.medication import scheduler
from app.services.medication.state_machine import (
    InvalidTransition,
    apply_self_report,
    apply_system,
)
from app.services.notifications import engine

from .conftest import ROSA, SAM, advance, auth


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- timezone and daylight-saving arithmetic ---------------------------------


def test_ordinary_day_uses_local_offset():
    assert scheduler.local_to_utc(date(2026, 11, 23), "08:00", "America/Chicago") == utc(2026, 11, 23, 14, 0)
    assert scheduler.local_to_utc(date(2026, 7, 1), "08:00", "America/Chicago") == utc(2026, 7, 1, 13, 0)


def test_spring_forward_gap_moves_dose_after_the_gap():
    # 02:30 does not exist on 2027-03-14 in Chicago; the dose lands at 03:30 CDT.
    assert scheduler.local_to_utc(date(2027, 3, 14), "02:30", "America/Chicago") == utc(2027, 3, 14, 8, 30)


def test_fall_back_repeated_hour_schedules_once_at_first_occurrence():
    # 01:30 happens twice on 2026-11-01; we take the first (CDT, UTC-5).
    assert scheduler.local_to_utc(date(2026, 11, 1), "01:30", "America/Chicago") == utc(2026, 11, 1, 6, 30)


def test_one_dose_per_day_across_dst_change(db):
    rosa = db.get(User, ROSA)
    rx = db.get(Prescription, "rx_d1001")
    rx.valid_from = date(2027, 3, 1)
    for schedule in rx.schedules:
        schedule.effective_from = date(2027, 3, 1)
    db.flush()
    scheduler.materialize(db, rosa, date(2027, 3, 13), date(2027, 3, 15))
    events = db.scalars(
        select(DoseEvent)
        .join(DoseSchedule)
        .where(DoseSchedule.prescription_id == "rx_d1001", DoseEvent.scheduled_at >= utc(2027, 3, 13))
        .order_by(DoseEvent.scheduled_at)
    ).all()
    assert [e.scheduled_at for e in events] == [
        utc(2027, 3, 13, 14, 0),  # 08:00 CST
        utc(2027, 3, 14, 13, 0),  # 08:00 CDT
        utc(2027, 3, 15, 13, 0),
    ]


# --- verified records only -----------------------------------------------------


def test_every_dose_event_traces_to_a_verified_prescription(db):
    events = db.scalars(select(DoseEvent)).all()
    assert events, "seed should produce dose events"
    for event in events:
        rx = event.schedule.prescription
        assert rx.status == "active" and rx.verified_by == SAM and rx.verified_at is not None
        assert event.schedule.local_time in rx.record_times


def test_unverified_prescription_has_no_schedule_and_no_doses(db):
    pending = db.get(Prescription, "rx_d1003")
    assert pending.status == "pending_verification"
    assert pending.schedules == []
    count = db.scalar(
        select(func.count()).select_from(DoseEvent).join(DoseSchedule)
        .where(DoseSchedule.prescription_id == pending.id)
    )
    assert count == 0


def test_only_a_verifier_can_verify_and_then_doses_appear(client, db):
    body = {"prescription_id": "rx_d1003"}
    assert client.post("/v1/medications/verify", json=body, headers=auth(ROSA)).status_code == 403
    assert client.post("/v1/medications/verify", json=body).status_code == 401
    ok = client.post("/v1/medications/verify", json=body, headers=auth(SAM))
    assert ok.status_code == 200 and ok.json()["status"] == "active"
    today = client.get("/v1/me/today", headers=auth(ROSA)).json()
    assert "atorvastatin" in [m["name"] for m in today["medications"]]
    # Verifying twice is refused.
    assert client.post("/v1/medications/verify", json=body, headers=auth(SAM)).status_code == 409


def test_verifier_cannot_enter_malformed_time(client):
    bad = client.post(
        "/v1/medications/verify",
        json={"prescription_id": "rx_d1003", "times": ["25:00"]},
        headers=auth(SAM),
    )
    assert bad.status_code == 422


# --- idempotency and restart persistence ---------------------------------------


def test_materialize_and_tick_are_idempotent(db):
    before = db.scalar(select(func.count()).select_from(DoseEvent))
    for _ in range(3):
        engine.tick(db)
    assert db.scalar(select(func.count()).select_from(DoseEvent)) == before
    advance(db, 10)  # 08:05, first reminder is out
    for _ in range(5):
        engine.tick(db)
    reminders = db.scalar(
        select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.kind == "dose_reminder")
    )
    assert reminders == 1


def test_reminders_persist_across_a_process_restart(tmp_path):
    """Pending work lives in the database, not in memory."""
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    original = str(database.engine.url.render_as_string(hide_password=False))
    try:
        database.configure(url)
        Base.metadata.create_all(database.engine)
        with SessionLocal() as session:
            seed.seed(session)
            engine.tick(session)
            events_before = session.scalar(select(func.count()).select_from(DoseEvent))
        database.engine.dispose()  # "the process died" before 08:00

        database.configure(url)  # "the process came back" after the dose time
        clock.advance(timedelta(minutes=40))  # 08:35
        with SessionLocal() as session:
            stats = engine.tick(session)
            assert session.scalar(select(func.count()).select_from(DoseEvent)) == events_before
            dose = session.scalar(
                select(DoseEvent).join(DoseSchedule).where(DoseSchedule.prescription_id == "rx_d1001")
                .order_by(DoseEvent.scheduled_at)
            )
            assert dose.status == DoseStatus.REMINDED and dose.reminders_sent == 3
            assert stats["delivered"] >= 3
        database.engine.dispose()
    finally:
        database.configure(original)


# --- state machine ---------------------------------------------------------------


def _first_dose(db) -> DoseEvent:
    return db.scalar(
        select(DoseEvent).join(DoseSchedule).where(DoseSchedule.prescription_id == "rx_d1001")
        .order_by(DoseEvent.scheduled_at)
    )


def test_there_is_no_verified_ingestion_state():
    assert "taken" not in DoseStatus.ALL
    assert not any("verified" in status for status in DoseStatus.ALL)


def test_silence_is_tracked_as_unconfirmed_never_as_missed_or_taken(db):
    dose = _first_dose(db)
    assert dose.status == DoseStatus.SCHEDULED
    seen = []
    for _ in range(60):  # five hours in five-minute steps
        advance(db, 5)
        db.refresh(dose)
        if not seen or seen[-1] != dose.status:
            seen.append(dose.status)
    assert seen == [DoseStatus.REMINDED, DoseStatus.OVERDUE, DoseStatus.UNKNOWN]
    assert dose.origin == "system" and dose.response_at is None
    assert dose.reminders_sent == 3


def test_system_cannot_mark_a_dose_taken_or_skipped(db):
    dose = _first_dose(db)
    for forbidden in (DoseStatus.SELF_REPORTED_TAKEN, DoseStatus.SKIPPED):
        with pytest.raises(InvalidTransition):
            apply_system(dose, forbidden)


def test_self_report_is_labelled_as_self_report(client, db):
    advance(db, 10)
    dose_id = _first_dose(db).id
    result = client.post(
        f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=auth(ROSA)
    ).json()
    assert result["status"] == "self_reported_taken"
    assert result["origin"] == "self_report" and result["self_reported"] is True
    assert result["independently_verified"] is False
    assert result["status_label"] == "Self-reported taken"


def test_late_self_report_and_correction_are_allowed(db):
    dose = _first_dose(db)
    advance(db, 300)
    db.refresh(dose)
    assert dose.status == DoseStatus.UNKNOWN
    apply_self_report(dose, "taken", clock.now())
    assert dose.status == DoseStatus.SELF_REPORTED_TAKEN
    apply_self_report(dose, "unsure", clock.now())
    assert dose.status == DoseStatus.UNKNOWN and dose.origin == "self_report"


def test_reported_dose_gets_no_more_reminders(client, db):
    advance(db, 10)
    dose_id = _first_dose(db).id
    client.post(f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=auth(ROSA))
    advance(db, 120)
    count = db.scalar(
        select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.resource_id == dose_id)
    )
    assert count == 1


def test_self_report_with_idempotency_key_is_applied_once(client, db):
    advance(db, 10)
    dose_id = _first_dose(db).id
    headers = {**auth(ROSA), "Idempotency-Key": "offline-queue-1"}
    first = client.post(f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=headers).json()
    advance(db, 30)
    replay = client.post(f"/v1/dose-events/{dose_id}/self-report", json={"status": "taken"}, headers=headers).json()
    assert replay["idempotent_replay"] is True
    assert replay["response_at"] == first["response_at"]


def test_invalid_report_value_is_rejected(client, db):
    dose_id = _first_dose(db).id
    bad = client.post(f"/v1/dose-events/{dose_id}/self-report", json={"status": "swallowed"}, headers=auth(ROSA))
    assert bad.status_code == 422
