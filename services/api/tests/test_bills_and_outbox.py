"""Acceptance: a reminder never marks a bill paid; payment claims carry
provenance; the outbox is durable, idempotent and retries."""
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app import clock
from app.models import Bill, BillEvent, BillStatus, NotificationOutbox, User
from app.services import billing
from app.services.notifications import outbox

from .conftest import ROSA, advance, auth, say


def _bill(db) -> Bill:
    return db.scalar(select(Bill).where(Bill.owner_id == ROSA))


def test_reminder_firing_never_changes_payment_status(db):
    bill = _bill(db)
    advance(db, 60 * 24 * 4, step=60)  # four days: reminder (lead 2 days) has fired
    db.refresh(bill)
    fired = db.scalars(select(BillEvent).where(BillEvent.bill_id == bill.id, BillEvent.kind == "reminder_fired")).all()
    assert len(fired) == 1
    assert bill.status == BillStatus.UPCOMING


def test_calendar_moves_open_states_but_never_to_paid(db):
    bill = _bill(db)
    seen = set()
    for _ in range(8):
        advance(db, 60 * 24, step=240)
        db.refresh(bill)
        seen.add(bill.status)
    assert seen == {BillStatus.UPCOMING, BillStatus.DUE, BillStatus.OVERDUE}


def test_exactly_one_automatic_reminder_per_bill(db):
    advance(db, 60 * 24 * 7, step=120)
    count = db.scalar(
        select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.kind == "bill_reminder")
    )
    assert count == 1


def test_self_report_and_verified_payment_are_different_states(client, db):
    bill = _bill(db)
    said = client.post(
        f"/v1/bills/{bill.id}/status", json={"status": "self_reported_paid"}, headers=auth(ROSA)
    ).json()
    assert said["status"] == "self_reported_paid"
    assert said["self_reported"] is True and said["independently_verified"] is False


def test_verified_paid_requires_a_verifying_source(client, db):
    bill = _bill(db)
    refused = client.post(
        f"/v1/bills/{bill.id}/status",
        json={"status": "verified_paid", "source": "self_report"},
        headers=auth(ROSA),
    )
    assert refused.status_code == 409
    confirmed = client.post(
        f"/v1/bills/{bill.id}/status",
        json={"status": "verified_paid", "source": "biller_confirmation", "note": "confirmation shown on statement"},
        headers=auth(ROSA),
    ).json()
    assert confirmed["independently_verified"] is True
    event = db.scalar(select(BillEvent).where(BillEvent.kind == "status:verified_paid"))
    assert event.source == "biller_confirmation"


def test_voice_can_only_self_report_payment(client, db):
    reply = say(client, "I paid the electric bill")
    assert "record_bill_self_report" in reply["tool_calls"]
    bill = _bill(db)
    assert bill.status == BillStatus.SELF_REPORTED_PAID
    assert "haven't checked" in reply["text"]


def test_self_report_can_be_taken_back(client, db):
    bill = _bill(db)
    client.post(f"/v1/bills/{bill.id}/status", json={"status": "self_reported_paid"}, headers=auth(ROSA))
    back = client.post(f"/v1/bills/{bill.id}/status", json={"status": "upcoming"}, headers=auth(ROSA)).json()
    assert back["status"] == "upcoming"


def test_paid_recurring_bill_creates_next_occurrence_once(client, db):
    bill = _bill(db)
    client.post(f"/v1/bills/{bill.id}/status", json={"status": "self_reported_paid"}, headers=auth(ROSA))
    client.post(
        f"/v1/bills/{bill.id}/status",
        json={"status": "verified_paid", "source": "bank_statement"},
        headers=auth(ROSA),
    )
    series = db.scalars(select(Bill).where(Bill.series_id == bill.series_id).order_by(Bill.due_date)).all()
    assert len(series) == 2
    assert series[1].status == BillStatus.UPCOMING and series[1].amount_cents is None
    assert series[1].due_date == billing.next_due_date(bill.due_date, "monthly", bill.anchor_day)


def test_monthly_recurrence_clamps_to_month_end_and_keeps_anchor():
    assert billing.next_due_date(date(2027, 1, 31), "monthly", 31) == date(2027, 2, 28)
    assert billing.next_due_date(date(2027, 2, 28), "monthly", 31) == date(2027, 3, 31)
    assert billing.next_due_date(date(2026, 12, 15), "monthly", 15) == date(2027, 1, 15)
    assert billing.next_due_date(date(2028, 2, 29), "yearly", 29) == date(2029, 2, 28)
    assert billing.next_due_date(date(2026, 11, 23), "none", 23) is None


def test_create_bill_validates_input(client):
    today = clock.now().date()
    ok = client.post(
        "/v1/bills",
        json={"payee_label": "Riverbend Water (synthetic)", "due_date": str(today + timedelta(days=9)), "recurrence": "monthly"},
        headers=auth(ROSA),
    )
    assert ok.status_code == 201 and ok.json()["status"] == "upcoming" and ok.json()["amount"] == ""
    bad = client.post(
        "/v1/bills",
        json={"payee_label": "", "due_date": "not-a-date", "amount_cents": -5},
        headers=auth(ROSA),
    )
    assert bad.status_code == 422


def test_bill_schema_has_no_place_for_credentials():
    columns = {column.name for column in Bill.__table__.columns}
    assert not columns & {"card_number", "password", "account_number", "routing_number", "cvv", "pin"}


def test_terminal_states_cannot_be_changed(db):
    bill, rosa = _bill(db), db.get(User, ROSA)
    billing.set_status(db, bill, rosa, status=BillStatus.CANCELLED, source="self_report")
    with pytest.raises(billing.InvalidBillTransition):
        billing.set_status(db, bill, rosa, status=BillStatus.SELF_REPORTED_PAID, source="self_report")


# --- outbox ----------------------------------------------------------------------


def _note(db, key="k1"):
    return outbox.enqueue(
        db, key=key, kind="test", target_user_id=ROSA, subject_user_id=ROSA,
        title="t", body="b", due_at=clock.now(),
    )


def test_enqueue_is_idempotent_on_key(db):
    assert _note(db) is not None
    assert _note(db) is None
    assert db.scalar(select(func.count()).select_from(NotificationOutbox).where(NotificationOutbox.idempotency_key == "k1")) == 1


def test_failed_delivery_is_retried_then_delivered(db):
    note = _note(db)
    attempts = []

    def flaky(n):
        attempts.append(n.id)
        if len(attempts) < 3:
            raise ConnectionError("push gateway down")

    assert outbox.dispatch(db, deliver=flaky) == 0
    assert note.status == "pending" and note.retry_count == 1 and note.due_at > clock.now()
    clock.advance(timedelta(minutes=2))
    outbox.dispatch(db, deliver=flaky)
    clock.advance(timedelta(minutes=2))
    assert outbox.dispatch(db, deliver=flaky) == 1
    assert note.status == "delivered" and note.retry_count == 2 and len(attempts) == 3


def test_delivery_gives_up_after_max_retries_and_says_so(db):
    note = _note(db)

    def down(n):
        raise TimeoutError()

    for _ in range(outbox.MAX_RETRIES + 2):
        clock.advance(timedelta(minutes=10))
        outbox.dispatch(db, deliver=down)
    assert note.status == "failed" and note.retry_count == outbox.MAX_RETRIES
    assert note.last_error == "TimeoutError"


def test_future_notifications_wait_until_due(client, db):
    say(client, "Remind me before my electricity bill is due.")
    extra = db.scalar(select(NotificationOutbox).where(NotificationOutbox.idempotency_key.like("bill:%:extra:%")))
    assert extra.status == "pending" and extra.due_at > clock.now()
    advance(db, 60 * 24 * 4 + 120, step=120)
    db.refresh(extra)
    assert extra.status == "delivered"
    assert _bill(db).status == BillStatus.UPCOMING  # reminded, not paid
