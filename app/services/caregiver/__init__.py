"""Consent grants and caregiver access checks.

The older adult owns every grant: which caregiver, which categories, whether
that caregiver may be alerted, and when it ends. All caregiver reads go
through `active_grant`, so there is one place where access is decided.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...models import SCOPES, CaregiverGrant, Role, User
from .. import audit


def active_grant(db: Session, senior_id: str, caregiver_id: str) -> CaregiverGrant | None:
    return db.scalar(
        select(CaregiverGrant).where(
            CaregiverGrant.senior_id == senior_id,
            CaregiverGrant.caregiver_id == caregiver_id,
            CaregiverGrant.revoked_at.is_(None),
        )
    )


def grants_for_caregiver(db: Session, caregiver_id: str) -> list[CaregiverGrant]:
    return list(
        db.scalars(
            select(CaregiverGrant).where(
                CaregiverGrant.caregiver_id == caregiver_id, CaregiverGrant.revoked_at.is_(None)
            )
        )
    )


def grants_for_senior(db: Session, senior_id: str, include_revoked: bool = False) -> list[CaregiverGrant]:
    query = select(CaregiverGrant).where(CaregiverGrant.senior_id == senior_id)
    if not include_revoked:
        query = query.where(CaregiverGrant.revoked_at.is_(None))
    return list(db.scalars(query.order_by(CaregiverGrant.granted_at.desc())))


def alert_recipients(db: Session, senior_id: str, scope: str) -> list[CaregiverGrant]:
    """Caregivers the older adult has allowed to be alerted about `scope`."""
    return [
        grant
        for grant in grants_for_senior(db, senior_id)
        if grant.alerts_enabled and scope in grant.scopes
    ]


def grant(
    db: Session, senior: User, caregiver: User, scopes: list[str], alerts_enabled: bool
) -> CaregiverGrant:
    if caregiver.role != Role.CAREGIVER:
        raise ValueError("Access can only be shared with a caregiver account.")
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown or not scopes:
        raise ValueError(f"Choose at least one of: {', '.join(SCOPES)}.")
    now = clock.now()
    existing = active_grant(db, senior.id, caregiver.id)
    if existing:
        # Replace rather than edit, so the history of what was shared survives.
        existing.revoked_at = now
    record = CaregiverGrant(
        senior_id=senior.id,
        caregiver_id=caregiver.id,
        scopes=sorted(set(scopes)),
        alerts_enabled=alerts_enabled,
        granted_at=now,
    )
    db.add(record)
    db.flush()
    audit.record(
        db,
        actor_id=senior.id,
        subject_user_id=senior.id,
        action="consent.grant",
        resource_type="caregiver_grant",
        resource_id=record.id,
        detail={"caregiver_id": caregiver.id, "scopes": record.scopes, "alerts": alerts_enabled},
    )
    return record


def revoke(db: Session, senior: User, caregiver_id: str) -> bool:
    existing = active_grant(db, senior.id, caregiver_id)
    if not existing:
        return False
    existing.revoked_at = clock.now()
    audit.record(
        db,
        actor_id=senior.id,
        subject_user_id=senior.id,
        action="consent.revoke",
        resource_type="caregiver_grant",
        resource_id=existing.id,
        detail={"caregiver_id": caregiver_id},
    )
    db.flush()
    return True
