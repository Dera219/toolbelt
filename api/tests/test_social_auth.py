"""Social sign-in linking rules.

The JWT signature check is PyJWT's job and is exercised by its own test suite;
what matters here is what we do with a *verified* identity — especially the
rules that decide whether a social login may take over an existing account.
"""

import pytest

from app.modules.identity import oidc
from app.modules.identity.oidc import Provider, SocialIdentity
from tests.conftest import login, register


@pytest.fixture()
def google(monkeypatch):
    """Stub verification: the token string names the fixture to return."""
    identities: dict[str, SocialIdentity] = {}

    def fake_verify(provider: Provider, id_token: str) -> SocialIdentity:
        if id_token not in identities:
            from fastapi import HTTPException, status

            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")
        return identities[id_token]

    monkeypatch.setattr(oidc, "verify_id_token", fake_verify)
    monkeypatch.setattr("app.modules.identity.router.oidc.verify_id_token", fake_verify)

    def add(token: str, *, sub: str, email: str | None, verified: bool = True, name=None):
        identities[token] = SocialIdentity(
            provider=Provider.GOOGLE,
            subject=sub,
            email=email,
            email_verified=verified,
            full_name=name,
        )
        return token

    return add


def _social(client, token: str, **extra):
    return client.post(
        "/auth/social", json={"provider": "google", "id_token": token, **extra}
    )


def test_first_sign_in_creates_account(client, google):
    google("tok-1" + "." + "x" * 40, sub="google-123", email="new@example.com", name="New Person")
    resp = _social(client, "tok-1" + "." + "x" * 40)
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    me = client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}).json()
    assert me["email"] == "new@example.com"
    assert me["full_name"] == "New Person"


def test_repeat_sign_in_returns_same_account(client, google):
    google("tok-1" + "." + "x" * 40, sub="google-123", email="new@example.com")
    first = _social(client, "tok-1" + "." + "x" * 40).json()
    me1 = client.get("/me", headers={"Authorization": f"Bearer {first['access_token']}"}).json()

    # Same subject, email later changed at the provider.
    google("tok-2" + "." + "x" * 40, sub="google-123", email="changed@example.com")
    second = _social(client, "tok-2" + "." + "x" * 40).json()
    me2 = client.get("/me", headers={"Authorization": f"Bearer {second['access_token']}"}).json()
    assert me1["id"] == me2["id"]


def test_verified_email_links_to_existing_password_account(client, google):
    register(client, "alice@example.com")
    password_headers = login(client, "alice@example.com")
    existing = client.get("/me", headers=password_headers).json()

    google("tok-1" + "." + "x" * 40, sub="google-999", email="alice@example.com", verified=True)
    social = _social(client, "tok-1" + "." + "x" * 40).json()
    me = client.get("/me", headers={"Authorization": f"Bearer {social['access_token']}"}).json()
    assert me["id"] == existing["id"]  # same account, now reachable both ways

    # And the password still works — linking must not lock the original method out.
    assert client.post(
        "/auth/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    ).status_code == 200


def test_unverified_email_cannot_take_over_an_account(client, google):
    """The core account-takeover guard."""
    register(client, "alice@example.com")

    google("evil" + "." + "x" * 40, sub="attacker-1", email="alice@example.com", verified=False)
    resp = _social(client, "evil" + "." + "x" * 40)
    assert resp.status_code == 400
    assert "verified" in resp.json()["detail"].lower()


def test_missing_email_is_rejected_with_guidance(client, google):
    google("no-email" + "." + "x" * 40, sub="apple-hidden", email=None)
    resp = _social(client, "no-email" + "." + "x" * 40)
    assert resp.status_code == 400
    assert "email" in resp.json()["detail"].lower()


def test_social_account_cannot_be_password_logged_in(client, google):
    """A social-only account gets an unusable password, not a guessable one."""
    google("tok-1" + "." + "x" * 40, sub="google-123", email="social@example.com")
    _social(client, "tok-1" + "." + "x" * 40)

    for attempt in ("", "password", "correct-horse-battery"):
        resp = client.post("/auth/login", json={"email": "social@example.com", "password": attempt})
        assert resp.status_code in (401, 422)


def test_role_applies_only_on_creation(client, google):
    google("tok-1" + "." + "x" * 40, sub="google-123", email="pro@example.com")
    first = _social(client, "tok-1" + "." + "x" * 40, role="worker").json()
    me = client.get("/me", headers={"Authorization": f"Bearer {first['access_token']}"}).json()
    assert me["role"] == "worker"

    # Signing in again with a different role must not silently change it.
    second = _social(client, "tok-1" + "." + "x" * 40, role="customer").json()
    me2 = client.get("/me", headers={"Authorization": f"Bearer {second['access_token']}"}).json()
    assert me2["role"] == "worker"


def test_invalid_token_rejected(client, google):
    assert _social(client, "not-a-real-token" + "." + "x" * 40).status_code == 401


def test_unknown_provider_rejected(client, google):
    resp = client.post(
        "/auth/social", json={"provider": "facebook", "id_token": "x" * 30}
    )
    assert resp.status_code == 400


def test_providers_endpoint_lists_only_configured(client):
    # Nothing is configured in tests, so the app shows no social buttons.
    assert client.get("/auth/providers").json() == {"providers": []}


def test_unconfigured_provider_refuses_tokens(client):
    """Without client IDs there is no audience to check, so tokens must not pass."""
    resp = client.post("/auth/social", json={"provider": "google", "id_token": "x" * 30})
    assert resp.status_code == 501
