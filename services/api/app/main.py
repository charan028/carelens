"""CareLens API.

Run from services/api:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import clock, sandbox, seed
from . import db as database
from .api.v1 import senior, shared
from .config import settings
from .db import Base, SessionLocal
from .services.notifications import engine

log = logging.getLogger("carelens")


def run_engine_once() -> None:
    """One reminder pass over every world this process is responsible for."""
    if not sandbox.enabled():
        with SessionLocal() as session:
            engine.tick(session)
        return
    sandbox.flush_last_seen()
    for sandbox_id in sandbox.active_ids():
        try:
            with clock.scope(sandbox_id), sandbox.session_factory(sandbox_id)() as session:
                engine.tick(session)
        except Exception:  # one broken sandbox must not stop the others
            log.exception("reminder engine pass failed for one sandbox")


async def _engine_loop() -> None:
    passes = 0
    while True:
        try:
            await asyncio.to_thread(run_engine_once)
            passes += 1
            if sandbox.enabled() and passes % 240 == 0:
                await asyncio.to_thread(sandbox.purge_expired)
        except Exception:  # keep reminding even if one pass fails
            # Type only: exception text could carry record contents.
            log.exception("reminder engine pass failed")
        await asyncio.sleep(settings.engine_interval_s)


def _report_startup() -> None:
    """Say plainly which database and mode this process is using, then test it.

    Printed once at startup so a wrong or missing connection string is obvious.
    The password is never printed.
    """
    out = logging.getLogger("uvicorn.error")
    info = database.describe_database()
    mode = "hosted (access code, private copy per tester)" if sandbox.enabled() else "local (one shared copy, no access code)"
    out.info("CareLens mode: %s", mode)
    out.info("CareLens database: %s at %s", info["kind"], info["location"])
    if "url" in info:
        out.info("CareLens database user: %s", info["user"])
        out.info("CareLens connection string (password hidden): %s", info["url"])
    for hint in info["hints"]:
        out.warning("CareLens database note: %s", hint)
    problem = database.check_connection()
    if problem:
        out.error("CareLens database connection FAILED: %s", problem)
        raise RuntimeError("Cannot reach the database. See the lines above for where it points and why it failed.")
    out.info("CareLens database connection: OK")


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.hosted_problems()
    if problems:
        raise RuntimeError("Refusing to start a hosted demo: " + "; ".join(problems))
    _report_startup()
    task = None
    if sandbox.enabled():
        sandbox.init()
    elif settings.is_demo:
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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # A demo with synthetic records has no business in a search index.
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
    if request.url.path.startswith("/v1/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


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
