"""Database engine, session factory and shared column types."""
from __future__ import annotations

import re
from datetime import timezone
from typing import AsyncIterator

from fastapi import Header

from sqlalchemy import DateTime, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from .config import settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend.

    SQLite drops tzinfo, so naive values are refused on the way in and UTC is
    re-attached on the way out. PostgreSQL stores `timestamptz` natively.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime refused; pass a timezone-aware value")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _explain_bad_url(url: str) -> str:
    """A readable reason for an unusable CARELENS_DATABASE_URL. Never echoes the value."""
    lines = [
        "",
        "CareLens cannot start: CARELENS_DATABASE_URL is not a database address.",
        "",
        "It must be one line in this form:",
        "  postgresql://postgres.<project-ref>:<password>@<pooler-host>:5432/postgres",
        "",
    ]
    if url.strip().lower().startswith("psql"):
        lines += [
            "What was given is a psql command, not an address. In Supabase, press Connect,",
            "set Type to URI and Method to Session pooler, and copy that line instead.",
            "Then replace [YOUR-PASSWORD] with your database password.",
        ]
    elif " " in url.strip():
        lines += ["What was given contains spaces, so it is probably a command or has extra text around it."]
    else:
        lines += ["Check for a missing 'postgresql://' at the start, or stray quotes or brackets."]
    lines += ["", "To use the local file database instead, run:  unset CARELENS_DATABASE_URL", ""]
    return "\n".join(lines)


def _make_engine(url: str):
    try:
        make_url(url)
    except Exception:
        raise SystemExit(_explain_bad_url(url))
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 15}}
        if make_url(url).database in (None, "", ":memory:"):
            # One shared connection, or every session would see its own empty DB.
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    # Works with any PostgreSQL, including hosted ones behind a connection
    # pooler (Supabase, Neon). Prepared statements are off because poolers in
    # transaction mode cannot keep them; connections are checked before use
    # and recycled so a paused or restarted database does not strand us.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
        connect_args={"prepare_threshold": None, "connect_timeout": 10},
    )


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def configure(url: str) -> None:
    """Point the service at another database (used by tests)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


async def get_db(x_sandbox: str | None = Header(default=None)) -> AsyncIterator[Session]:
    """One session per request, on the caller's own sandbox when sandboxes are on.

    Async on purpose: it runs in the request's own context before the route
    does, so the sandbox it enters is the one the route and its clock see.
    """
    from . import sandbox  # imported late: sandbox imports this module

    session = sandbox.session_factory_for_request(x_sandbox)()
    try:
        yield session
    finally:
        session.close()


def describe_database() -> dict:
    """Where the app is pointed, safe to print: the password is never included."""
    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite":
        return {"kind": "SQLite file", "location": url.database or "in memory", "hints": []}
    host = url.host or ""
    hints = []
    if host.startswith("db.") and host.endswith(".supabase.co"):
        hints.append(
            "This is Supabase's direct connection, which is IPv6-only without the IPv4 add-on. "
            "If connecting fails or times out, use the Session pooler string instead."
        )
    elif host.endswith(".pooler.supabase.com"):
        mode = "transaction" if url.port == 6543 else "session"
        hints.append(f"Supabase shared pooler, {mode} mode.")
        if not (url.username or "").startswith("postgres."):
            hints.append("Pooler user names look like postgres.<project-ref>; this one does not.")
    if url.password in (None, "") or re.search(r"YOUR-.*PASSWORD", url.password or ""):
        hints.append(
            "The password is missing or is still Supabase's [YOUR-...PASSWORD] placeholder. "
            "Replace the placeholder, brackets included, with your real database password."
        )
    return {
        "kind": "PostgreSQL",
        "location": f"{host}:{url.port or 5432}/{url.database}",
        "user": url.username or "",
        "url": url.render_as_string(hide_password=True),
        "hints": hints,
    }


def check_connection() -> str | None:
    """Try one trivial query. Returns None when it works, else a short reason."""
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return None
    except Exception as error:  # report any driver or network failure the same way
        reason = str(getattr(error, "orig", error)).strip().splitlines()[0][:300]
        password = make_url(settings.database_url).password
        if password:
            reason = reason.replace(password, "***")
        return f"{type(getattr(error, 'orig', error)).__name__}: {reason}"
