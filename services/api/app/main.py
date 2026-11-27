"""CareLens API.

Run from services/api:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import seed
from . import db as database
from .api.v1 import senior, shared
from .config import settings
from .db import Base, SessionLocal
from .services.notifications import engine

log = logging.getLogger("carelens")


def run_engine_once() -> None:
    with SessionLocal() as session:
        engine.tick(session)


async def _engine_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_engine_once)
        except Exception:  # keep reminding even if one pass fails
            # Type only: exception text could carry record contents.
            log.exception("reminder engine pass failed")
        await asyncio.sleep(settings.engine_interval_s)


def _configure_intent_model() -> None:
    out = logging.getLogger("uvicorn.error")
    if not settings.intent_llm:
        out.info("CareLens intent model: rules only (set ANTHROPIC_API_KEY to use Claude)")
        return
    from .ai import assistant, llm
    from .ai.intent import ConstrainedLLMIntentModel

    assistant.set_intent_model(ConstrainedLLMIntentModel(llm.make_completer(settings.intent_model)))
    out.info("CareLens intent model: %s, rules as fallback", settings.intent_model)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None
    _configure_intent_model()
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


def _password_ok(header: str) -> bool:
    if not header.lower().startswith("basic "):
        return False
    try:
        given = base64.b64decode(header[6:]).decode().partition(":")[2]
    except Exception:
        return False
    return hmac.compare_digest(given, settings.demo_password)


@app.middleware("http")
async def demo_password(request: Request, call_next):
    """A public demo link shares one copy; a single password keeps strangers out."""
    if settings.demo_password and request.url.path != "/health":
        if not _password_ok(request.headers.get("authorization", "")):
            return Response(
                "This CareLens demo needs a password. Any name, then the password you were given.",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="CareLens demo"'},
            )
    return await call_next(request)


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
