"""Single source of time for the whole service.

Every deterministic service asks this module for "now", so tests and the demo
can move time without touching the system clock. The offset can only be
changed through the demo-only /v1/dev endpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def set_now(target: datetime) -> None:
    global _offset
    if target.tzinfo is None:
        raise ValueError("clock target must be timezone-aware")
    _offset = target.astimezone(timezone.utc) - datetime.now(timezone.utc)


def advance(delta: timedelta) -> None:
    global _offset
    _offset += delta


def reset() -> None:
    global _offset
    _offset = timedelta(0)


def is_simulated() -> bool:
    return abs(_offset) > timedelta(seconds=1)
