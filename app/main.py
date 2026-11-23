"""CareLens API.

Run from services/api:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db as database
from . import seed
from .api.v1 import senior, shared
from .config import settings
from .db import Base, SessionLocal
from .services.notifications import engine

log = logging.getLogger("carelens")


async def _engine_loop() -> None:
    while True:
        try:
            with SessionLocal() as session:
                await asyncio.to_thread(engine.tick, session)
        except Exception:  # keep reminding even if one pass fails
            # Type only: exception text could carry record contents.
            log.exception("reminder engine pass failed")
        await asyncio.sleep(settings.engine_interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    if settings.is_demo:
        # Demo convenience. Real deployments run `alembic upgrade head`.
        Base.metadata.create_all(database.engine)
        with SessionLocal() as session:
            seed.seed_if_empty(session)
    if settings.run_engine_loop:
        task = asyncio.create_task(_engine_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="CareLens API",
    version="0.1.0",
    description="Proof of concept. Synthetic data only. Not a medical device, "
    "not an emergency service, and it never pays bills.",
    lifespan=lifespan,
)
app.include_router(senior.router)
app.include_router(shared.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}


if settings.web_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=settings.web_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str):
        if path.startswith(("v1/", "health")):
            return JSONResponse({"detail": "Not found."}, status_code=404)
        candidate = (settings.web_dist / path).resolve()
        if path and candidate.is_file() and settings.web_dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(settings.web_dist / "index.html")
