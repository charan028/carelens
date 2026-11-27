"""Single source of time for the whole service.

Every deterministic service asks this module for "now", so tests and the demo
can move time without touching the system clock. Each sandbox (one tester's
private copy of the demo) has its own offset, so one tester moving their
clock never moves anyone else's. The offset can only be changed through the
demo-only /v1/dev endpoints.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator

LOCAL = "local"  # the single world used when sandboxes are off

_sandbox: ContextVar[str] = ContextVar("carelens_sandbox", default=LOCAL)
_offsets: dict[str, timedelta] = {}
_listener: Callable[[str, timedelta], None] | None = None


def sandbox() -> str:
    """The sandbox the current request or engine pass is working in."""
    return _sandbox.get()


def enter(sandbox_id: str) -> None:
    _sandbox.set(sandbox_id)


@contextmanager
def scope(sandbox_id: str) -> Iterator[None]:
    token = _sandbox.set(sandbox_id)
    try:
        yield
    finally:
        _sandbox.reset(token)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offsets.get(sandbox(), timedelta(0))


def _store(offset: timedelta) -> None:
    _offsets[sandbox()] = offset
    if _listener:
        _listener(sandbox(), offset)


def set_now(target: datetime) -> None:
    if target.tzinfo is None:
        raise ValueError("clock target must be timezone-aware")
    _store(target.astimezone(timezone.utc) - datetime.now(timezone.utc))


def advance(delta: timedelta) -> None:
    _store(_offsets.get(sandbox(), timedelta(0)) + delta)


def reset() -> None:
    _store(timedelta(0))


def is_simulated() -> bool:
    return abs(_offsets.get(sandbox(), timedelta(0))) > timedelta(seconds=1)


# --- persistence hooks (used by app.sandbox) ----------------------------------


def restore(sandbox_id: str, offset: timedelta) -> None:
    """Load a saved offset at startup, without notifying the listener."""
    _offsets[sandbox_id] = offset


def forget(sandbox_id: str) -> None:
    _offsets.pop(sandbox_id, None)


def on_change(listener: Callable[[str, timedelta], None] | None) -> None:
    global _listener
    _listener = listener
