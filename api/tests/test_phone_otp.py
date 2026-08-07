import re
from datetime import timedelta

from app.core.db import SessionLocal
from app.modules.identity.models import PhoneVerification, utcnow
from app.modules.identity.sms import DevSmsSender
from tests.conftest import login, register

PHONE = "+13015550123"


def _request_code(client, headers, phone=PHONE) -> str:
    DevSmsSender.outbox.clear()
    resp = client.post(
        "/me/phone/request-verification", json={"phone": phone}, headers=headers
    )
    assert resp.status_code == 202, resp.text
    sent_to, message = DevSmsSender.outbox[-1]
    assert sent_to == phone
    return re.search(r"\b(\d{6})\b", message).group(1)


def test_otp_happy_path(client):
    register(client, "alice@example.com")
    headers = login(client, "alice@example.com")
    code = _request_code(client, headers)

    me = client.post("/me/phone/verify", json={"code": code}, headers=headers).json()
    assert me["phone"] == PHONE
    assert me["phone_verified"] is True


def test_wrong_code_and_attempt_limit(client):
    register(client, "alice@example.com")
    headers = login(client, "alice@example.com")
    code = _request_code(client, headers)
    wrong = "000000" if code != "000000" else "111111"

    for _ in range(5):
        resp = client.post("/me/phone/verify", json={"code": wrong}, headers=headers)
        assert resp.status_code == 400
    # 6th attempt is locked out even with the right code.
    resp = client.post("/me/phone/verify", json={"code": code}, headers=headers)
    assert resp.status_code == 429


def test_expired_code_rejected(client):
    register(client, "alice@example.com")
    headers = login(client, "alice@example.com")
    code = _request_code(client, headers)

    with SessionLocal() as db:
        challenge = db.query(PhoneVerification).one()
        challenge.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()

    resp = client.post("/me/phone/verify", json={"code": code}, headers=headers)
    assert resp.status_code == 400


def test_resend_cooldown(client):
    register(client, "alice@example.com")
    headers = login(client, "alice@example.com")
    _request_code(client, headers)
    resp = client.post(
        "/me/phone/request-verification", json={"phone": PHONE}, headers=headers
    )
    assert resp.status_code == 429


def test_dev_sms_outbox_gated_outside_dev(client):
    register(client, "alice@example.com")
    headers = login(client, "alice@example.com")
    # Environment is "test" here, so the dev-only endpoint must not exist.
    assert client.get("/dev/sms-outbox", headers=headers).status_code == 404


def test_phone_uniqueness_enforced(client):
    register(client, "alice@example.com")
    alice = login(client, "alice@example.com")
    code = _request_code(client, alice)
    client.post("/me/phone/verify", json={"code": code}, headers=alice)

    register(client, "bob@example.com")
    bob = login(client, "bob@example.com")
    resp = client.post("/me/phone/request-verification", json={"phone": PHONE}, headers=bob)
    assert resp.status_code == 409
