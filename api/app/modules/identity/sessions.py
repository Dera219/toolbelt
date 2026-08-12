"""Refresh-token sessions: issue, rotate, revoke.

Access tokens stay short-lived (15 min). The refresh token is the long-lived
credential, stored only as a hash, rotated on every use, and revoked family-wide
if a used token is replayed.
"""

import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.identity.models import RefreshToken, User, UserStatus, utcnow


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def issue(db: Session, user: User, family_id: str | None = None) -> str:
    settings = get_settings()
    token = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash(token),
            family_id=family_id or secrets.token_hex(16),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    db.flush()
    return token


def _revoke_family(db: Session, family_id: str) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


class SessionReplayed(Exception):
    """A spent refresh token was presented again. The family has been revoked in
    the caller's transaction, which must be committed — so the endpoint returns
    401 as a response rather than raising."""


def rotate(db: Session, token: str) -> tuple[User, str]:
    """Exchange a refresh token for a fresh one. Returns (user, new_token)."""
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(token)))
    if record is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if record.revoked_at is not None:
        # Replay of an already-rotated token: assume theft and kill the family.
        # Raising here would roll the revocation back with the request, so the
        # caller is told to answer 401 *without* an exception — see the router.
        _revoke_family(db, record.family_id)
        db.flush()
        raise SessionReplayed
    if utcnow() > _aware(record.expires_at):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    user = db.get(User, record.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    record.revoked_at = utcnow()
    new_token = issue(db, user, family_id=record.family_id)
    return user, new_token


def revoke(db: Session, token: str) -> None:
    """Sign out. Unknown or already-revoked tokens succeed silently — logging out
    must never fail, and a 404 here would confirm which tokens exist."""
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(token)))
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow()
        db.flush()


def revoke_all_for_user(db: Session, user: User) -> int:
    result = db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    db.flush()
    return result.rowcount or 0
