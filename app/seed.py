"""Load the synthetic fixtures in data/synthetic/seed.json.

Dates in the fixture are relative ("due in 5 days"), so the demo always opens
on a realistic day. Verified prescriptions go through the same
`verify_prescription` path the API uses; one is left unverified to show that
nothing is scheduled until a verifier confirms it.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from . import clock, db as database
from .config import settings
from .db import Base
from .models import (
    Appointment,
    CaregiverGrant,
    DrugContentCache,
    MedicationCatalog,
    Prescription,
    User,
)
from .services import billing
from .services.medication import scheduler

DEFAULT_TIMEZONE = "America/Chicago"


def load_fixture() -> dict:
    return json.loads((settings.synthetic_dir / "seed.json").read_text())


def seed(db: Session) -> None:
    data = load_fixture()
    tz = data.get("timezone", DEFAULT_TIMEZONE)
    now = clock.now()
    today = scheduler.local_day(now, tz)

    users = {}
    for item in data["users"]:
        users[item["id"]] = User(timezone=tz, is_synthetic=True, **item)
        db.add(users[item["id"]])
    for item in data["catalog"]:
        db.add(MedicationCatalog(**item))
    db.flush()

    for item in data["grants"]:
        db.add(CaregiverGrant(granted_at=now, **item))

    for item in data["prescriptions"]:
        verifier_id = item.pop("verified_by")
        rx = Prescription(valid_from=today, **item)
        db.add(rx)
        db.flush()
        if verifier_id:
            scheduler.verify_prescription(db, rx, users[verifier_id])

    for item in data["bills"]:
        billing.create_bill(
            db,
            users[item["owner_id"]],
            payee_label=item["payee_label"],
            due_date=today + timedelta(days=item["due_in_days"]),
            amount_cents=item.get("amount_cents"),
            recurrence=item.get("recurrence", "none"),
            reminder_lead_days=item.get("reminder_lead_days", 2),
        )

    for item in data["appointments"]:
        db.add(
            Appointment(
                owner_id=item["owner_id"],
                title=item["title"],
                kind=item["kind"],
                location=item["location"],
                starts_at=scheduler.local_to_utc(
                    today + timedelta(days=item["in_days"]), item["local_time"], tz
                ),
                reminder_lead_minutes=item["reminder_lead_minutes"],
            )
        )

    for card in data["reference_cards"]:
        # Bundled cards expire immediately, so a live source always wins.
        db.add(DrugContentCache(origin="bundled", fetched_at=now, expires_at=now, **card))
    db.commit()


def seed_if_empty(db: Session) -> bool:
    if db.get(User, "usr_rosa") is not None:
        return False
    seed(db)
    return True


def reset_and_seed(db: Session) -> None:
    db.rollback()
    db.close()
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    seed(db)
