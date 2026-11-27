"""Create the schema (if needed) and load the synthetic fixtures.

    python scripts/seed_synthetic_data.py          # seed if empty
    python scripts/seed_synthetic_data.py --reset  # wipe and reseed

Honors CARELENS_DATABASE_URL. For PostgreSQL run `alembic upgrade head` in
services/api first; this script only creates tables for the SQLite demo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app import db as database, seed  # noqa: E402
from app.db import Base, SessionLocal  # noqa: E402

if __name__ == "__main__":
    Base.metadata.create_all(database.engine)
    with SessionLocal() as session:
        if "--reset" in sys.argv:
            seed.reset_and_seed(session)
            print("Reset and loaded synthetic fixtures.")
        elif seed.seed_if_empty(session):
            print("Loaded synthetic fixtures.")
        else:
            print("Database already has data; nothing changed. Use --reset to wipe it.")
