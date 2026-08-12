"""Sign in with Google / Apple / Microsoft.

The client performs the provider's sign-in flow and hands us the resulting **ID
token**. We verify that token's signature against the provider's published JWKS
and check issuer, audience, and expiry — then mint our own session. We never see
the user's provider password, and we never trust claims the client sends us
directly.

Why ID tokens rather than an authorization-code exchange: the client is a mobile
app, so a code flow would need a client secret we cannot safely ship. Verifying a
signed ID token server-side gives the same assurance without the secret.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.core.config import get_settings


class Provider(str, enum.Enum):
    GOOGLE = "google"
    APPLE = "apple"
    MICROSOFT = "microsoft"


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderConfig:
    issuers: tuple[str, ...]
    jwks_uri: str
    label: str


PROVIDERS: dict[Provider, ProviderConfig] = {
    Provider.GOOGLE: ProviderConfig(
        issuers=("https://accounts.google.com", "accounts.google.com"),
        jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
        label="Google",
    ),
    Provider.APPLE: ProviderConfig(
        issuers=("https://appleid.apple.com",),
        jwks_uri="https://appleid.apple.com/auth/keys",
        label="Apple",
    ),
    Provider.MICROSOFT: ProviderConfig(
        # Multi-tenant Microsoft issues per-tenant issuers, so the tenant id
        # segment is checked with a prefix/suffix match rather than equality.
        issuers=("https://login.microsoftonline.com/",),
        jwks_uri="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        label="Microsoft",
    ),
}


@dataclass(frozen=True)
class SocialIdentity:
    provider: Provider
    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None


# One JWKS client per provider: it caches signing keys, so building a new one per
# request would fetch Google's key set on every sign-in.
_jwk_clients: dict[Provider, PyJWKClient] = {}


def _jwk_client(provider: Provider) -> PyJWKClient:
    if provider not in _jwk_clients:
        _jwk_clients[provider] = PyJWKClient(PROVIDERS[provider].jwks_uri, cache_keys=True)
    return _jwk_clients[provider]


def configured_audiences(provider: Provider) -> list[str]:
    settings = get_settings()
    raw = {
        Provider.GOOGLE: settings.google_client_ids,
        Provider.APPLE: settings.apple_client_ids,
        Provider.MICROSOFT: settings.microsoft_client_ids,
    }[provider]
    return [item.strip() for item in raw.split(",") if item.strip()]


def enabled_providers() -> list[str]:
    """Providers with client IDs configured. The app hides the rest."""
    return [p.value for p in Provider if configured_audiences(p)]


def _check_issuer(provider: Provider, issuer: str) -> bool:
    config = PROVIDERS[provider]
    if provider is Provider.MICROSOFT:
        return issuer.startswith("https://login.microsoftonline.com/") and issuer.endswith(
            "/v2.0"
        )
    return issuer in config.issuers


def check_signing_support() -> None:
    """Fail loudly at startup if RS256 verification is unavailable.

    PyJWT silently ships without `cryptography`, and the only symptom is every
    social sign-in returning "invalid token" at runtime — which reads like a
    misconfigured client ID and sends you looking in the wrong place.
    """
    if not configured_audiences_any():
        return
    if "RS256" not in jwt.algorithms.get_default_algorithms():
        raise RuntimeError(
            "Social sign-in is configured but PyJWT cannot verify RS256 tokens. "
            "Install the crypto extra:  pip install 'PyJWT[crypto]'"
        )


def configured_audiences_any() -> bool:
    return any(configured_audiences(p) for p in Provider)


def verify_id_token(provider: Provider, id_token: str) -> SocialIdentity:
    audiences = configured_audiences(provider)
    if not audiences:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{PROVIDERS[provider].label} sign-in is not configured on this server",
        )
    try:
        signing_key = _jwk_client(provider).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audiences,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Log why — never the token itself. Without this the operator sees only
        # "invalid token" and cannot tell an audience mismatch from a bad clock.
        logger.warning(
            "%s ID token rejected: %s: %s",
            PROVIDERS[provider].label,
            type(exc).__name__,
            exc,
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail=f"Invalid {PROVIDERS[provider].label} token"
        ) from exc
    except Exception as exc:  # JWKS fetch failures and similar
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not verify with {PROVIDERS[provider].label}. Try again.",
        ) from exc

    if not _check_issuer(provider, claims.get("iss", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Unexpected token issuer")

    email = claims.get("email")
    # Providers disagree on the type here: Google sends a bool, Apple a string.
    verified_claim = claims.get("email_verified", False)
    email_verified = verified_claim in (True, "true", "True")

    name = claims.get("name") or " ".join(
        part for part in (claims.get("given_name"), claims.get("family_name")) if part
    )
    return SocialIdentity(
        provider=provider,
        subject=str(claims["sub"]),
        email=email.lower() if isinstance(email, str) else None,
        email_verified=bool(email_verified),
        full_name=name or None,
    )
