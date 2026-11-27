"""Durable notification outbox.

Every reminder and alert is first written to the database with an idempotency
key, then delivered by `dispatch`. A crash between the two steps loses
nothing: the row is still pending on restart. Writing the same key twice is a
no-op, so duplicate engine runs cannot double-notify.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import NotificationOutbox

MAX_RETRIES = 5
RETRY_BACKOFF = timedelta(seconds=30)

Deliver = Callable[[NotificationOutbox], None]


def in_app_delivery(note: NotificationOutbox) -> None:
    """Default channel: the row itself is what phone and dashboard clients poll.

    Push (FCM/APNs), SMS or email adapters plug in here later.
    """
    return None


def enqueue(
    db: Session,
    *,
    key: str,
    kind: str,
    target_user_id: str,
    subject_user_id: str,
    title: str,
    body: str,
    due_at: datetime,
    resource_type: str = "",
    resource_id: str = "",
) -> NotificationOutbox | None:
    """Queue a notification once. Returns None when the key already exists."""
    if db.scalar(select(NotificationOutbox.id).where(NotificationOutbox.idempotency_key == key)):
        return None
    note = NotificationOutbox(
        idempotency_key=key,
        kind=kind,
        target_user_id=target_user_id,
        subject_user_id=subject_user_id,
        title=title,
        body=body,
        due_at=due_at,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.add(note)
    db.flush()
    return note


def dispatch(db: Session, deliver: Deliver = in_app_delivery, now: datetime | None = None) -> int:
    now = now or clock.now()
    due = db.scalars(
        select(NotificationOutbox)
        .where(NotificationOutbox.status == "pending", NotificationOutbox.due_at <= now)
        .order_by(NotificationOutbox.due_at)
    ).all()
    delivered = 0
    for note in due:
        try:
            deliver(note)
        except Exception as error:  # delivery adapters may fail in any way
            note.retry_count += 1
            note.last_error = type(error).__name__
            if note.retry_count >= MAX_RETRIES:
                note.status = "failed"
            else:
                note.due_at = now + RETRY_BACKOFF * note.retry_count
            continue
        note.status = "delivered"
        note.delivered_at = now
        delivered += 1
    db.flush()
    return delivered
