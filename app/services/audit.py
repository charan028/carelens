"""Audit trail. Records actor, action, resource and outcome only.

Callers pass identifiers and state names, never medication names, directions
or spoken text, so the audit log stays free of clinical content.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from .. import clock
from ..models import AuditEvent


def record(
    db: Session,
    *,
    actor_id: str,
    subject_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str = "",
    outcome: str = "ok",
    detail: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id,
        subject_user_id=subject_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        detail=detail or {},
        at=clock.now(),
    )
    db.add(event)
    return event
