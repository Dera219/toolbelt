"""Refresh-token rotation, replay detection, and rate limiting."""

from datetime import timedelta

from app.core.db import SessionLocal
from app.modules.identity.models import RefreshToken, utcnow
from tests.conftest import register

CREDS = {"email": "alice@example.com", "password": "correct-horse-battery"}


def _login(client) -> dict:
    resp = client.post("/auth/login", json=CREDS)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_login_issues_both_tokens(client):
    register(client, CREDS["email"])
    tokens = _login(client)
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["access_token"] != tokens["refresh_token"]
    assert client.get("/me", headers=_bearer(tokens["access_token"])).status_code == 200


def test_refresh_rotates_and_keeps_session_alive(client):
    register(client, CREDS["email"])
    first = _login(client)

    refreshed = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert refreshed.status_code == 200
    second = refreshed.json()
    assert second["refresh_token"] != first["refresh_token"]  # rotated
    assert client.get("/me", headers=_bearer(second["access_token"])).status_code == 200

    # The old refresh token is now spent.
    assert (
        client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code
        == 401
    )


def test_replayed_token_kills_the_whole_family(client):
    """Reuse of a rotated token means it leaked — every session in that family dies."""
    register(client, CREDS["email"])
    first = _login(client)
    second = client.post(
        "/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    # Attacker replays the spent token.
    assert (
        client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code
        == 401
    )
    # The legitimate client's current token is revoked too — forces a real sign-in.
    assert (
        client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code
        == 401
    )


def test_logout_revokes_only_that_session(client):
    register(client, CREDS["email"])
    phone = _login(client)
    laptop = _login(client)

    assert client.post("/auth/logout", json={"refresh_token": phone["refresh_token"]}).status_code == 204
    assert (
        client.post("/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code
        == 401
    )
    # The other device is untouched.
    assert (
        client.post("/auth/refresh", json={"refresh_token": laptop["refresh_token"]}).status_code
        == 200
    )


def test_logout_is_idempotent_and_quiet(client):
    register(client, CREDS["email"])
    tokens = _login(client)
    for _ in range(2):
        assert (
            client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code
            == 204
        )
    # An unknown token must not reveal anything either.
    assert client.post("/auth/logout", json={"refresh_token": "x" * 40}).status_code == 204


def test_revoke_all_sessions(client):
    register(client, CREDS["email"])
    first = _login(client)
    second = _login(client)

    resp = client.post("/me/sessions/revoke-all", headers=_bearer(first["access_token"]))
    assert resp.status_code == 200
    assert resp.json()["revoked"] == 2

    for tokens in (first, second):
        assert (
            client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code
            == 401
        )


def test_expired_refresh_token_rejected(client):
    register(client, CREDS["email"])
    tokens = _login(client)

    with SessionLocal() as db:
        record = db.query(RefreshToken).one()
        record.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()

    assert (
        client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code
        == 401
    )


def test_refresh_token_is_never_stored_in_the_clear(client):
    register(client, CREDS["email"])
    tokens = _login(client)
    with SessionLocal() as db:
        stored = db.query(RefreshToken).one()
        assert stored.token_hash != tokens["refresh_token"]
        assert len(stored.token_hash) == 64  # sha256 hex


def test_login_is_rate_limited(client):
    register(client, CREDS["email"])
    wrong = {"email": CREDS["email"], "password": "wrong-password-xyz"}

    # 10 attempts per 5 minutes, then the door closes — for right answers too.
    for _ in range(10):
        assert client.post("/auth/login", json=wrong).status_code == 401
    blocked = client.post("/auth/login", json=CREDS)
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After") is not None


def test_rate_limits_are_scoped_per_endpoint(client):
    """Exhausting login must not lock a user out of unrelated endpoints."""
    register(client, CREDS["email"])
    tokens = _login(client)
    for _ in range(12):
        client.post("/auth/login", json={"email": CREDS["email"], "password": "nope-nope-nope"})

    assert client.get("/me", headers=_bearer(tokens["access_token"])).status_code == 200
