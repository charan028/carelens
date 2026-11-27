"""Authentication and role checks.

Demo-grade: signed bearer tokens for seeded synthetic personas. The token
format and the `current_user` dependency are the seam where a real identity
provider replaces this before any real-user pilot.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Callable

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User


def _sign(user_id: str) -> str:
    return hmac.new(settings.secret_key.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:40]


def issue_token(user_id: str) -> str:
    return f"{user_id}.{_sign(user_id)}"


def verify_token(token: str) -> str | None:
    user_id, _, signature = token.rpartition(".")
    if user_id and hmac.compare_digest(signature, _sign(user_id)):
        return user_id
    return None


def current_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    user_id = verify_token(authorization[7:].strip())
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="This sign-in is not valid.")
    return user


def require_role(*roles: str) -> Callable[..., User]:
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your account cannot do this.")
        return user

    return dependency
