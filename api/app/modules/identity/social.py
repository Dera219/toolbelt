"""Turning a verified social identity into a ToolBelt session.

The linking rules matter more than the plumbing:

* The provider's `sub` is the identity. Match on it first — it survives email
  changes, and Apple only sends an email on the very first sign-in.
* Auto-link to an existing password account by email **only if the provider says
  that email is verified**. Otherwise anyone could register `you@gmail.com` at a
  sloppy provider and inherit your account.
* A social-only account gets an unusable password hash rather than an empty one,
  so the password login path can never match it.
"""

import secrets

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.identity.models import OAuthIdentity, User, UserRole, UserStatus
from app.modules.identity.oidc import PROVIDERS, SocialIdentity


def _unusable_password() -> str:
    """A real bcrypt hash of a random secret nobody holds. Using a blank or
    sentinel value risks a future code path treating it as 'no password set'."""
    return hash_password(secrets.token_urlsafe(32))


def sign_in(db: Session, identity: SocialIdentity, role: UserRole | None = None) -> User:
    provider = identity.provider.value

    link = db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == provider, OAuthIdentity.subject == identity.subject
        )
    )
    if link is not None:
        user = db.get(User, link.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
        if identity.email and link.email != identity.email:
            link.email = identity.email  # provider-side email change
        db.flush()
        return user

    # No link yet. Adopt an existing account only on a provider-verified email.
    user: User | None = None
    if identity.email and identity.email_verified:
        user = db.scalar(select(User).where(User.email == identity.email))
        if user is not None and user.status != UserStatus.ACTIVE:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")

    if user is None:
        if not identity.email:
            # Apple private relay always supplies one; a provider that sends
            # nothing leaves us no way to contact or de-duplicate the account.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{PROVIDERS[identity.provider].label} did not share an email "
                    "address. Enable email sharing, or sign up with an email."
                ),
            )
        if not identity.email_verified:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{PROVIDERS[identity.provider].label} has not verified that "
                    "email address."
                ),
            )
        user = User(
            email=identity.email,
            password_hash=_unusable_password(),
            full_name=identity.full_name or identity.email.split("@")[0],
            role=role or UserRole.CUSTOMER,
        )
        db.add(user)
        db.flush()

    db.add(
        OAuthIdentity(
            user_id=user.id,
            provider=provider,
            subject=identity.subject,
            email=identity.email,
        )
    )
    db.flush()
    return user


def linked_providers(db: Session, user: User) -> list[str]:
    return list(
        db.scalars(select(OAuthIdentity.provider).where(OAuthIdentity.user_id == user.id))
    )
