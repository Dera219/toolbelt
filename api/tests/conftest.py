import os
import pathlib
import sys

_TESTS_DIR = pathlib.Path(__file__).parent
_API_DIR = _TESTS_DIR.parent
sys.path.insert(0, str(_API_DIR))

os.environ["TOOLBELT_DATABASE_URL"] = f"sqlite:///{_TESTS_DIR / 'test_toolbelt.db'}"
os.environ["TOOLBELT_JWT_SECRET"] = "test-secret-not-for-prod-0123456789abcdef"
os.environ["TOOLBELT_ENVIRONMENT"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

# College Park, MD area coordinates used across tests.
UMD = {"lat": 38.9897, "lng": -76.9378}
DC = {"lat": 38.9072, "lng": -77.0369}  # ~10.6 km from UMD
BALTIMORE = {"lat": 39.2904, "lng": -76.6122}  # ~44 km from UMD


@pytest.fixture()
def clean_db():
    from app.modules.payments.provider import _fake_provider

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    _fake_provider.reset()
    yield


@pytest.fixture()
def client(clean_db):
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str, role: str = "customer", **overrides) -> dict:
    body = {
        "email": email,
        "password": "correct-horse-battery",
        "full_name": "Test User",
        "role": role,
        **overrides,
    }
    resp = client.post("/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def login(client: TestClient, email: str, password: str = "correct-horse-battery") -> dict:
    resp = client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def add_payment_method(client: TestClient, headers: dict) -> None:
    resp = client.post(
        "/me/payment-method", json={"payment_method_ref": "pm_fake_card"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


def customer_with_pm(client: TestClient, email: str) -> dict:
    """Register a customer with a payment method on file — able to accept offers."""
    register(client, email)
    headers = login(client, email)
    add_payment_method(client, headers)
    return headers


def make_worker(
    client: TestClient, email: str, trade: str = "cleaning", lat: float | None = None,
    lng: float | None = None, verified: bool = True,
) -> dict:
    register(client, email, role="worker")
    headers = login(client, email)
    resp = client.put(
        "/me/worker-profile",
        json={
            "trade": trade,
            "hourly_rate_cents": 4500,
            "base_lat": lat if lat is not None else UMD["lat"],
            "base_lng": lng if lng is not None else UMD["lng"],
            "service_radius_km": 30,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    if verified:
        set_vetting_status(email, "verified")
    return headers


def set_vetting_status(email: str, vetting_status: str) -> None:
    """Operational shortcut for tests — the API path is covered in test_vetting.py."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.modules.identity.models import User, VettingStatus, WorkerProfile

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        profile = db.get(WorkerProfile, user.id)
        profile.vetting_status = VettingStatus(vetting_status)
        db.commit()


def make_admin(client: TestClient, email: str = "admin@example.com") -> dict:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.modules.identity.models import User

    register(client, email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.is_admin = True
        db.commit()
    return login(client, email)


def post_job(client: TestClient, headers: dict, trade: str = "cleaning", **overrides) -> dict:
    body = {
        "trade": trade,
        "title": "Deep clean 2BR apartment",
        "description": "Kitchen and both bathrooms.",
        "lat": UMD["lat"],
        "lng": UMD["lng"],
        "address_text": "College Park, MD",
        "budget_cents": 12000,
        **overrides,
    }
    resp = client.post("/jobs", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
