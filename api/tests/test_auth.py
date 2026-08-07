from tests.conftest import login, register


def test_register_and_login(client):
    user = register(client, "alice@example.com")
    assert user["email"] == "alice@example.com"
    assert "password" not in user and "password_hash" not in user

    headers = login(client, "alice@example.com")
    me = client.get("/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["id"] == user["id"]


def test_duplicate_email_rejected(client):
    register(client, "alice@example.com")
    resp = client.post(
        "/auth/register",
        json={
            "email": "ALICE@example.com",  # case-insensitive duplicate
            "password": "correct-horse-battery",
            "full_name": "Imposter",
        },
    )
    assert resp.status_code == 409


def test_wrong_password_rejected(client):
    register(client, "alice@example.com")
    resp = client.post(
        "/auth/login", json={"email": "alice@example.com", "password": "wrong-password-123"}
    )
    assert resp.status_code == 401


def test_short_password_rejected(client):
    resp = client.post(
        "/auth/register",
        json={"email": "bob@example.com", "password": "short", "full_name": "Bob"},
    )
    assert resp.status_code == 422


def test_invalid_phone_rejected(client):
    resp = client.post(
        "/auth/register",
        json={
            "email": "bob@example.com",
            "password": "correct-horse-battery",
            "full_name": "Bob",
            "phone": "301-555-0123",  # not E.164
        },
    )
    assert resp.status_code == 422


def test_me_requires_auth(client):
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_customer_cannot_create_worker_profile(client):
    register(client, "cust@example.com", role="customer")
    headers = login(client, "cust@example.com")
    resp = client.put(
        "/me/worker-profile",
        json={"trade": "cleaning", "hourly_rate_cents": 4000, "base_lat": 38.9, "base_lng": -76.9},
        headers=headers,
    )
    assert resp.status_code == 403


def test_unknown_trade_rejected(client):
    register(client, "w@example.com", role="worker")
    headers = login(client, "w@example.com")
    resp = client.put(
        "/me/worker-profile",
        json={"trade": "astronaut", "hourly_rate_cents": 4000, "base_lat": 38.9, "base_lng": -76.9},
        headers=headers,
    )
    assert resp.status_code == 422
