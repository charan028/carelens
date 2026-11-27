"""Sign-in, drug reference, caregiver, verifier and demo-only endpoints."""
from __future__ import annotations

from datetime import timedelta

import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock, sandbox, seed
from ...auth import current_user, issue_token, require_role
from ...config import settings
from ...db import get_db
from ...models import Prescription, PrescriptionStatus, Role, User
from ...schemas import ClockChange, DemoLogin, SandboxRequest, VerifyPrescription
from ...services import audit, caregiver, today
from ...services.medication import education, scheduler
from ...services.notifications import engine
from ..deps import rate_limit

router = APIRouter(prefix="/v1")


# --- hosted demo: access code and private sandbox ----------------------------

_attempts: dict[str, deque] = {}
ATTEMPTS_PER_MINUTE = 8


def _throttle(request: Request) -> None:
    """Slow down access-code guessing. Per caller address, one-minute window."""
    forwarded = request.headers.get("x-forwarded-for", "")
    caller = forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")
    window = _attempts.setdefault(caller, deque())
    now = time.monotonic()
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= ATTEMPTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Too many tries. Wait a minute and try again.")
    window.append(now)
    if len(_attempts) > 5000:  # keep memory bounded
        _attempts.clear()


@router.get("/auth/config", tags=["auth"])
def auth_config():
    """What the web app needs to know before it can show anything."""
    return {"sandboxes": sandbox.enabled(), "access_code_required": sandbox.enabled()}


@router.post("/sandbox", tags=["auth"], status_code=201)
def create_sandbox(body: SandboxRequest, request: Request):
    """Trade the access code for a private copy of the synthetic demo."""
    if not sandbox.enabled():
        raise HTTPException(status_code=404, detail="Not available.")
    _throttle(request)
    if not sandbox.access_code_ok(body.access_code):
        raise HTTPException(status_code=403, detail="That access code isn't right.")
    try:
        sandbox_id = sandbox.create()
    except sandbox.SandboxLimitReached:
        raise HTTPException(
            status_code=503, detail="The demo is full right now. Try again later."
        )
    return {"sandbox_token": sandbox.token_for(sandbox_id), "notice": today.SYNTHETIC_NOTICE}


# --- sign-in (demo personas only) -------------------------------------------


@router.get("/auth/demo-users", tags=["auth"])
def demo_users(db: Session = Depends(get_db)):
    if not settings.is_demo:
        raise HTTPException(status_code=404, detail="Not available.")
    order = {Role.SENIOR: 0, Role.CAREGIVER: 1, Role.VERIFIER: 2}
    users = sorted(
        db.scalars(select(User).where(User.is_synthetic.is_(True))).all(),
        key=lambda u: (order.get(u.role, 9), u.display_name),
    )
    return {
        "notice": today.SYNTHETIC_NOTICE,
        "users": [{"id": u.id, "display_name": u.display_name, "role": u.role} for u in users],
    }


@router.post("/auth/demo-login", tags=["auth"])
def demo_login(body: DemoLogin, db: Session = Depends(get_db)):
    if not settings.is_demo:
        raise HTTPException(status_code=404, detail="Not available.")
    user = db.get(User, body.user_id)
    if user is None or not user.is_synthetic:
        raise HTTPException(status_code=404, detail="That demo account was not found.")
    return {
        "token": issue_token(user.id),
        "user": {"id": user.id, "display_name": user.display_name, "role": user.role},
    }


# --- public drug reference ---------------------------------------------------


@router.get("/drugs/search", tags=["reference"])
def drug_search(
    q: str = Query(min_length=2, max_length=80),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rate_limit(user, "reference")
    return education.search_drugs(db, q)


@router.get("/drugs/{rxcui}/education", tags=["reference"])
def drug_education(rxcui: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    rate_limit(user, "reference")
    if not rxcui.isdigit() or len(rxcui) > 12:
        raise HTTPException(status_code=422, detail="An RxCUI is a number.")
    info = education.get_education(db, rxcui)
    if info is None:
        raise HTTPException(status_code=404, detail="No approved reference for that RxCUI.")
    return info


# --- caregiver ---------------------------------------------------------------


@router.get("/caregiver/summary", tags=["caregiver"])
def caregiver_summary(user: User = Depends(require_role(Role.CAREGIVER)), db: Session = Depends(get_db)):
    grants = caregiver.grants_for_caregiver(db, user.id)
    for grant in grants:
        audit.record(
            db, actor_id=user.id, subject_user_id=grant.senior_id, action="caregiver.view",
            resource_type="caregiver_grant", resource_id=grant.id, detail={"scopes": grant.scopes},
        )
    views = [today.caregiver_view(db, grant) for grant in grants]
    db.commit()
    return {
        "caregiver": {"id": user.id, "display_name": user.display_name},
        "seniors": views,
        "notice": "Statuses are what the person reported. CareLens cannot confirm a dose was "
        "taken and is not an emergency monitoring service.",
    }


# --- verifier (pharmacist) ---------------------------------------------------


def _rx_dict(rx: Prescription, db: Session) -> dict:
    owner = db.get(User, rx.user_id)
    return {
        "prescription_id": rx.id,
        "patient": owner.display_name,
        "rxcui": rx.rxcui,
        "name": rx.catalog.name,
        "directions": rx.verbatim_directions,
        "source": rx.source,
        "record_times": rx.record_times,
        "status": rx.status,
        "verified_at": rx.verified_at.isoformat() if rx.verified_at else None,
    }


@router.get("/medications/verification-queue", tags=["verifier"])
def verification_queue(user: User = Depends(require_role(Role.VERIFIER)), db: Session = Depends(get_db)):
    rows = db.scalars(select(Prescription).order_by(Prescription.status.desc())).all()
    return {"prescriptions": [_rx_dict(rx, db) for rx in rows]}


@router.post("/medications/verify", tags=["verifier"])
def verify_medication(
    body: VerifyPrescription,
    user: User = Depends(require_role(Role.VERIFIER)),
    db: Session = Depends(get_db),
):
    rx = db.get(Prescription, body.prescription_id)
    if rx is None:
        raise HTTPException(status_code=404, detail="That prescription record was not found.")
    if rx.status != PrescriptionStatus.PENDING:
        raise HTTPException(status_code=409, detail="This record is not waiting for verification.")
    try:
        scheduler.verify_prescription(db, rx, user, body.times)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    db.commit()
    engine.tick(db)
    return _rx_dict(rx, db)


# --- demo controls (synthetic environments only) -----------------------------


def _demo_only() -> None:
    if not settings.is_demo:
        raise HTTPException(status_code=404, detail="Not available.")


def _set_local_time(db: Session, value: str) -> None:
    from zoneinfo import ZoneInfo

    senior = db.scalar(select(User).where(User.role == Role.SENIOR))
    tz = ZoneInfo(senior.timezone if senior else seed.DEFAULT_TIMEZONE)
    hour, minute = (int(part) for part in value.split(":"))
    clock.set_now(clock.now().astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0))


@router.post("/dev/clock", tags=["demo"], dependencies=[Depends(_demo_only)])
def dev_clock(body: ClockChange, db: Session = Depends(get_db)):
    """Move the simulated clock, then run the reminder engine."""
    if body.reset:
        clock.reset()
    if body.set_local_time:
        _set_local_time(db, body.set_local_time)
    if body.advance_minutes:
        # Step through time so every reminder fires in order, as it would live.
        remaining = body.advance_minutes
        while remaining > 0:
            step = min(5, remaining)
            clock.advance(timedelta(minutes=step))
            engine.tick(db)
            remaining -= step
    stats = engine.tick(db)
    return {"now": clock.now().isoformat(), "simulated": clock.is_simulated(), "engine": stats}


@router.post("/dev/reset", tags=["demo"], dependencies=[Depends(_demo_only)])
def dev_reset(body: ClockChange | None = None, db: Session = Depends(get_db)):
    """Wipe everything and reload the synthetic fixtures.

    With `set_local_time`, the day restarts at that time (for example 07:55,
    just before the morning reminder).
    """
    clock.reset()
    if body and body.set_local_time:
        _set_local_time(db, body.set_local_time)
    education.reset_outage_memory()
    seed.reset_and_seed(db)
    stats = engine.tick(db)
    return {"ok": True, "now": clock.now().isoformat(), "engine": stats, "notice": today.SYNTHETIC_NOTICE}
