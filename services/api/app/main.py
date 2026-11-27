"""CareLens API.

Run from services/api:  uvicorn app.main:app --port 8000
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
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


# --- shared demo password (public link) --------------------------------------
# A cookie, not HTTP Basic: browsers don't attach Basic credentials to the
# app's background fetch() calls, so the page would load and the API would 401.

DEMO_COOKIE = "carelens_demo"
_PASSWORD_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>CareLens demo</title>
<style>body{margin:0;background:#f7f1e6;color:#1f1a14;font:18px/1.45 system-ui,sans-serif}
main{max-width:26rem;margin:12vh auto;padding:28px;background:#fffcf6;border:3px solid #1f1a14;border-radius:22px}
h1{font-size:1.6rem;margin:0 0 8px}p{margin:0 0 16px;color:#5b534a}label{display:block;font-weight:700}
input{width:100%%;box-sizing:border-box;font:inherit;font-size:1.2rem;padding:10px 12px;border:2px solid #5b534a;border-radius:10px;margin:6px 0 14px}
button{font:inherit;font-weight:700;padding:12px 18px;border-radius:10px;border:2px solid #1d6b3d;background:#1d6b3d;color:#fff;cursor:pointer;min-height:48px}
.err{color:#a8321c;font-weight:700;margin-bottom:12px}</style></head><body><main>
<h1>CareLens demo</h1><p>Synthetic data only. Enter the password you were given.</p>%s
<form method="post" action="/demo-password"><label for="p">Password</label>
<input id="p" name="password" type="password" autocomplete="current-password" autofocus required>
<button>Open the demo</button></form></main></body></html>"""


def _cookie_value() -> str:
    # The cookie never carries the password itself.
    return hmac.new(settings.secret_key.encode(), settings.demo_password.encode(), hashlib.sha256).hexdigest()


def _has_demo_cookie(request: Request) -> bool:
    return hmac.compare_digest(request.cookies.get(DEMO_COOKIE, ""), _cookie_value())


@app.middleware("http")
async def demo_password(request: Request, call_next):
    """A public demo link shares one copy; one password (as a cookie) keeps strangers out."""
    path = request.url.path
    # Icons and the manifest are fetched by the browser without cookies; nothing private in them.
    public = path in ("/health", "/demo-password", "/manifest.json") or path.endswith(".png")
    if not settings.demo_password or public or _has_demo_cookie(request):
        return await call_next(request)
    if path.startswith("/v1/"):
        return JSONResponse({"detail": "This demo needs its password. Reload the page."}, status_code=401)
    return HTMLResponse(_PASSWORD_PAGE % "", status_code=401)


@app.post("/demo-password", include_in_schema=False)
async def demo_password_form(request: Request):
    fields = parse_qs((await request.body()).decode("utf-8", "replace"))
    given = (fields.get("password") or [""])[0]
    if not settings.demo_password or not hmac.compare_digest(given, settings.demo_password):
        return HTMLResponse(_PASSWORD_PAGE % '<p class="err">That password isn\'t right.</p>', status_code=401)
    response = RedirectResponse("/", status_code=303)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(DEMO_COOKIE, _cookie_value(), max_age=60 * 60 * 24 * 30, httponly=True, secure=secure, samesite="lax")
    return response


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
