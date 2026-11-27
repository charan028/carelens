"""Private sandboxes for a hosted demo.

A public URL must not put every visitor in the same world, where one person's
"Restart the day" wipes another's session. With CARELENS_SANDBOXES=1 each
tester gets, behind an access code:

* their own copy of the synthetic records (a PostgreSQL schema, or a SQLite
  file when running sandboxes locally),
* their own simulated clock,
* sign-in tokens that only work inside that copy.

With sandboxes off (local development and tests) there is one world, "local",
on the configured database, and nothing here is used.

Assumes one API process. Several processes would need the in-memory indexes
below moved to the database.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import Float, String, create_engine, delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from . import clock, db as database
from .config import settings
from .db import Base, UTCDateTime

_ID = re.compile(r"^[0-9a-f]{12}$")


class RegistryBase(DeclarativeBase):
    """Separate metadata: the registry lives outside every sandbox."""


class SandboxRow(RegistryBase):
    __tablename__ = "demo_sandboxes"
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    clock_offset_s: Mapped[float] = mapped_column(Float, default=0.0)


class SandboxLimitReached(Exception):
    pass


_lock = threading.Lock()
_last_seen: dict[str, datetime] = {}
_engines: dict[str, Engine] = {}
_factories: dict[str, sessionmaker] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    return settings.sandboxes


# --- tokens -------------------------------------------------------------------


def _signature(sandbox_id: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), f"sandbox:{sandbox_id}".encode(), hashlib.sha256
    ).hexdigest()[:40]


def token_for(sandbox_id: str) -> str:
    return f"{sandbox_id}.{_signature(sandbox_id)}"


def verify_token(token: str | None) -> str | None:
    sandbox_id, _, signature = (token or "").partition(".")
    if _ID.match(sandbox_id) and hmac.compare_digest(signature, _signature(sandbox_id)):
        return sandbox_id
    return None


def access_code_ok(candidate: str) -> bool:
    return hmac.compare_digest(candidate.strip().encode(), settings.access_code.encode())


# --- storage ------------------------------------------------------------------


def _is_postgres() -> bool:
    return database.engine.dialect.name == "postgresql"


def _schema(sandbox_id: str) -> str:
    return f"sb_{sandbox_id}"


def _engine_for(sandbox_id: str) -> Engine:
    if sandbox_id in _engines:
        return _engines[sandbox_id]
    if _is_postgres():
        # Same connection pool; every table name is redirected to the sandbox schema.
        engine = database.engine.execution_options(
            schema_translate_map={None: _schema(sandbox_id)}
        )
    elif database.engine.url.database in (None, "", ":memory:"):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    else:
        settings.sandbox_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            f"sqlite:///{settings.sandbox_dir / (sandbox_id + '.db')}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
    _engines[sandbox_id] = engine
    _factories[sandbox_id] = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def session_factory(sandbox_id: str) -> sessionmaker:
    _engine_for(sandbox_id)
    return _factories[sandbox_id]


REGISTRY_SCHEMA = "carelens_demo"


def _registry_engine() -> Engine:
    """Where the sandbox list lives.

    On PostgreSQL it gets its own schema instead of `public`, because some
    hosts (Supabase) can publish `public` tables through an auto-generated API.
    """
    if _is_postgres():
        return database.engine.execution_options(schema_translate_map={None: REGISTRY_SCHEMA})
    return database.engine


def _registry() -> Session:
    return Session(_registry_engine(), expire_on_commit=False)


def _save_offset(sandbox_id: str, offset: timedelta) -> None:
    if sandbox_id == clock.LOCAL:
        return
    with _registry() as registry:
        row = registry.get(SandboxRow, sandbox_id)
        if row is not None:
            row.clock_offset_s = offset.total_seconds()
            registry.commit()


# --- lifecycle ----------------------------------------------------------------


def init() -> None:
    """Create the registry and reload sandboxes that survived a restart."""
    if _is_postgres():
        with database.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{REGISTRY_SCHEMA}"'))
    RegistryBase.metadata.create_all(_registry_engine())
    with _registry() as registry:
        for row in registry.scalars(select(SandboxRow)).all():
            _last_seen[row.id] = row.last_seen_at
            clock.restore(row.id, timedelta(seconds=row.clock_offset_s))
    clock.on_change(_save_offset)
    purge_expired()


def create() -> str:
    """Make a new sandbox with freshly seeded synthetic records."""
    from . import seed  # imported late: seed imports the service layer

    with _lock:
        if len(_last_seen) >= settings.sandbox_max:
            purge_expired()
        if len(_last_seen) >= settings.sandbox_max:
            raise SandboxLimitReached()
        sandbox_id = secrets.token_hex(6)
        now = _utcnow()
        with _registry() as registry:
            registry.add(SandboxRow(id=sandbox_id, created_at=now, last_seen_at=now))
            registry.commit()
        _last_seen[sandbox_id] = now

    engine = _engine_for(sandbox_id)
    if _is_postgres():
        with database.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_schema(sandbox_id)}"'))
    Base.metadata.create_all(engine)
    with clock.scope(sandbox_id), session_factory(sandbox_id)() as session:
        seed.seed(session)
    return sandbox_id


def drop(sandbox_id: str) -> None:
    engine = _engines.pop(sandbox_id, None)
    _factories.pop(sandbox_id, None)
    _last_seen.pop(sandbox_id, None)
    clock.forget(sandbox_id)
    if _is_postgres():
        with database.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{_schema(sandbox_id)}" CASCADE'))
    else:
        if engine is not None:
            engine.dispose()
        (settings.sandbox_dir / f"{sandbox_id}.db").unlink(missing_ok=True)
    with _registry() as registry:
        registry.execute(delete(SandboxRow).where(SandboxRow.id == sandbox_id))
        registry.commit()


def purge_expired() -> int:
    cutoff = _utcnow() - timedelta(days=settings.sandbox_idle_days)
    expired = [sandbox_id for sandbox_id, seen in _last_seen.items() if seen < cutoff]
    for sandbox_id in expired:
        drop(sandbox_id)
    return len(expired)


def flush_last_seen() -> None:
    """Persist "last used" times so idle sandboxes can be cleaned up after a restart."""
    snapshot = dict(_last_seen)
    with _registry() as registry:
        for row in registry.scalars(select(SandboxRow)).all():
            seen = snapshot.get(row.id)
            if seen and seen > row.last_seen_at:
                row.last_seen_at = seen
        registry.commit()


def active_ids(within: timedelta = timedelta(hours=48)) -> list[str]:
    """Sandboxes worth running the reminder engine for."""
    cutoff = _utcnow() - within
    return [sandbox_id for sandbox_id, seen in _last_seen.items() if seen >= cutoff]


def count() -> int:
    return len(_last_seen)


# --- request entry point -------------------------------------------------------


def session_factory_for_request(token: str | None) -> sessionmaker:
    """Pick the database for this request and enter its sandbox.

    Called from the `get_db` dependency, so no route can reach data without
    passing through here.
    """
    if not enabled():
        return database.SessionLocal
    sandbox_id = verify_token(token)
    if sandbox_id is None or sandbox_id not in _last_seen:
        # 401 with a marker the web app uses to show the access screen again.
        raise HTTPException(
            status_code=401,
            detail="Your demo session has ended. Enter the access code to start a new one.",
            headers={"X-CareLens-Sandbox": "required"},
        )
    _last_seen[sandbox_id] = _utcnow()
    clock.enter(sandbox_id)
    return session_factory(sandbox_id)


def reset_for_tests() -> None:
    for sandbox_id in list(_last_seen):
        drop(sandbox_id)
    _engines.clear()
    _factories.clear()
