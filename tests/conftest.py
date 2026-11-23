"""Test setup: in-memory database, no network, a fixed simulated clock."""
import os

os.environ["CARELENS_ENV"] = "test"
os.environ["CARELENS_DATABASE_URL"] = os.environ.get("CARELENS_TEST_DATABASE_URL", "sqlite:///:memory:")
os.environ["CARELENS_RUN_ENGINE"] = "0"
os.environ["CARELENS_LIVE_REFERENCE_APIS"] = "0"
os.environ["CARELENS_RATE_LIMIT"] = "100000"

from datetime import datetime, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clock, db as database, seed  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, SessionLocal  # noqa: E402
from app.integrations import base as reference_base  # noqa: E402
from app.main import app  # noqa: E402
from app.services.medication import education  # noqa: E402
from app.services.notifications import engine  # noqa: E402

CHICAGO = ZoneInfo("America/Chicago")
# A Monday morning, five minutes before the first seeded dose (08:00).
START = datetime(2026, 11, 23, 7, 55, tzinfo=CHICAGO)

ROSA, DANIEL, PRIYA, SAM = "usr_rosa", "usr_daniel", "usr_priya", "usr_sam"


@pytest.fixture(autouse=True)
def fresh_world():
    clock.set_now(START)
    settings.live_reference_apis = False
    reference_base.set_client(None)
    education.reset_outage_memory()
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    with SessionLocal() as session:
        seed.seed(session)
        engine.tick(session)
    yield
    clock.reset()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {issue_token(user_id)}"}


def advance(db, minutes: int, step: int = 5) -> None:
    """Move the simulated clock forward, running the engine as it would live."""
    remaining = minutes
    while remaining > 0:
        jump = min(step, remaining)
        clock.advance(timedelta(minutes=jump))
        engine.tick(db)
        remaining -= jump


def say(client, text: str, context: dict | None = None, user: str = ROSA) -> dict:
    response = client.post(
        "/v1/assistant/message", json={"text": text, "context": context or {}}, headers=auth(user)
    )
    assert response.status_code == 200, response.text
    return response.json()
