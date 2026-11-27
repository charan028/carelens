"""Shared request helpers: idempotent mutations and rate limiting."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import IdempotencyRecord, User


def idempotent(db: Session, user: User, route: str, key: str | None, action: Callable[[], dict]) -> dict:
    """Run `action` once per (user, route, Idempotency-Key).

    A retried request (for example an offline queue replaying after reconnect)
    gets the stored response instead of repeating the mutation.
    """
    if not key:
        return action()
    record_key = f"{user.id}:{route}:{key}"[:200]
    stored = db.get(IdempotencyRecord, record_key)
    if stored is not None:
        return {**stored.response, "idempotent_replay": True}
    response = action()
    db.add(IdempotencyRecord(key=record_key, response=response, created_at=clock.now()))
    db.commit()
    return response


_calls: dict[str, deque] = defaultdict(deque)


def rate_limit(user: User, bucket: str = "assistant") -> None:
    """Sliding one-minute window per user. In-process; fine for one demo node."""
    window = _calls[f"{bucket}:{clock.sandbox()}:{user.id}"]
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings.assistant_rate_limit_per_min:
        raise HTTPException(status_code=429, detail="Too many requests. Wait a minute and try again.")
    window.append(now)
