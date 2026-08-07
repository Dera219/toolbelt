"""Phone OTP flow: request → SMS with 6-digit code → verify. Codes are HMAC-hashed
at rest, expire after 10 minutes, allow 5 attempts, and re-requests are throttled."""

import hashlib
import hmac
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.identity.models import PhoneVerification, User, utcnow
from app.modules.identity.sms import get_sms_sender

CODE_TTL = timedelta(minutes=10)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    secret = get_settings().jwt_secret.encode()
    return hmac.new(secret, code.encode(), hashlib.sha256).hexdigest()


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _phone_taken(db: Session, phone: str, user_id: int) -> bool:
    other = db.scalar(select(User.id).where(User.phone == phone, User.id != user_id))
    return other is not None


def request_verification(db: Session, user: User, phone: str) -> None:
    if _phone_taken(db, phone, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Phone already registered")

    latest = db.scalar(
        select(PhoneVerification)
        .where(PhoneVerification.user_id == user.id, PhoneVerification.consumed_at.is_(None))
        .order_by(PhoneVerification.created_at.desc())
        .limit(1)
    )
    now = utcnow()
    if latest is not None and now - _aware(latest.created_at) < RESEND_COOLDOWN:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="Wait before requesting another code"
        )

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        PhoneVerification(
            user_id=user.id,
            phone=phone,
            code_hash=_hash_code(code),
            expires_at=now + CODE_TTL,
        )
    )
    db.flush()
    get_sms_sender().send(phone, f"Your ToolBelt verification code is {code}")


def verify_code(db: Session, user: User, code: str) -> User:
    challenge = db.scalar(
        select(PhoneVerification)
        .where(PhoneVerification.user_id == user.id, PhoneVerification.consumed_at.is_(None))
        .order_by(PhoneVerification.created_at.desc())
        .limit(1)
    )
    if challenge is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No pending verification")
    if utcnow() > _aware(challenge.expires_at):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Code expired; request a new one")
    if challenge.attempts >= MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts; request a new code"
        )
    if not hmac.compare_digest(challenge.code_hash, _hash_code(code)):
        challenge.attempts += 1
        # Commit now: the HTTPException below rolls back the request transaction,
        # and the attempt counter must survive failed requests or lockout never trips.
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Incorrect code")
    if _phone_taken(db, challenge.phone, user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Phone already registered")

    challenge.consumed_at = utcnow()
    user.phone = challenge.phone
    user.phone_verified = True
    db.flush()
    return user
