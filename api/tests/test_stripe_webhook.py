"""POST /webhooks/stripe — real Stripe-Signature verification and payload
translation. The domain flow underneath (idempotency, flush_pending_payouts)
is shared with /webhooks/payments, which test_payments.py covers via the
internal event shape."""

import hashlib
import hmac
import json
import time

import pytest

from app.core.config import get_settings
from app.modules.payments.provider import _fake_provider
from tests.conftest import customer_with_pm, make_worker, post_job

STRIPE_WEBHOOK_SECRET = "whsec_test_0123456789abcdef0123456789abcdef"


@pytest.fixture()
def stripe_secret():
    # get_settings() is lru_cached, so mutate the live instance and restore it;
    # setting the env var here would be invisible to the already-built Settings.
    settings = get_settings()
    previous = settings.stripe_webhook_secret
    settings.stripe_webhook_secret = STRIPE_WEBHOOK_SECRET
    yield
    settings.stripe_webhook_secret = previous


@pytest.fixture()
def no_stripe_secret():
    """The unconfigured case, stated rather than inherited.

    A developer with a real `whsec_…` in their gitignored .env had this test
    asserting against their own machine's configuration, so it failed there and
    passed in CI. Pinning the value here is the same move the fixture above
    makes in the other direction, and it makes the test say what it means: with
    no secret, the route refuses everything.
    """
    settings = get_settings()
    previous = settings.stripe_webhook_secret
    settings.stripe_webhook_secret = None
    yield
    settings.stripe_webhook_secret = previous


def _sign(raw: bytes, secret: str = STRIPE_WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def _post_event(client, event: dict, header: str | None = None):
    raw = json.dumps(event).encode()
    return client.post(
        "/webhooks/stripe",
        content=raw,
        headers={
            "Stripe-Signature": header if header is not None else _sign(raw),
            "Content-Type": "application/json",
        },
    )


def _account_updated(
    account_ref: str, capability_status: str = "active", event_id: str = "evt_stripe_1"
) -> dict:
    """account.updated for a v2 core account: the account nests under
    data.object and payouts-enabled lives at
    configuration.recipient.capabilities.stripe_balance.stripe_transfers.status.
    """
    return {
        "id": event_id,
        "type": "account.updated",
        "data": {
            "object": {
                "id": account_ref,
                "configuration": {
                    "recipient": {
                        "capabilities": {
                            "stripe_balance": {
                                "stripe_transfers": {"status": capability_status}
                            }
                        }
                    }
                },
            }
        },
    }


def _worker_with_captured_payment(client):
    """Job completed before onboarding finished: funds held on the worker's
    ledger balance, payment stuck in `captured` until payouts are enabled."""
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    assert client.post(f"/offers/{offer['id']}/accept", headers=customer).status_code == 200
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    account = client.post("/me/payout-account", headers=worker).json()
    assert account["payouts_enabled"] is False
    return job, worker, account["provider_account_ref"]


def test_activation_enables_payouts_and_flushes(stripe_secret, client):
    job, worker, account_ref = _worker_with_captured_payment(client)
    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "captured"

    resp = _post_event(client, _account_updated(account_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "processed"

    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is True
    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "paid_out"
    assert _fake_provider.transfers[-1]["amount_cents"] == 8500
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0


def test_pending_capability_does_not_enable(stripe_secret, client):
    job, worker, account_ref = _worker_with_captured_payment(client)
    resp = _post_event(client, _account_updated(account_ref, capability_status="pending"))
    assert resp.status_code == 200
    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is False
    assert _fake_provider.transfers == []


def test_capability_revocation_disables_payouts(stripe_secret, client):
    _, worker, account_ref = _worker_with_captured_payment(client)
    _post_event(client, _account_updated(account_ref, event_id="evt_enable"))
    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is True

    resp = _post_event(
        client, _account_updated(account_ref, capability_status="inactive", event_id="evt_disable")
    )
    assert resp.status_code == 200
    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is False


def test_v1_shaped_payload_still_translates(stripe_secret, client):
    """Flat `payouts_enabled` bool on the account object (Accounts v1 shape)."""
    _, worker, account_ref = _worker_with_captured_payment(client)
    event = {
        "id": "evt_v1_shape",
        "type": "account.updated",
        "data": {"object": {"id": account_ref, "payouts_enabled": True}},
    }
    assert _post_event(client, event).status_code == 200
    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is True


def test_wrong_secret_rejected(stripe_secret, client):
    event = _account_updated("acct_whatever")
    raw = json.dumps(event).encode()
    resp = _post_event(client, event, header=_sign(raw, secret="whsec_wrong_secret"))
    assert resp.status_code == 400

    for garbage in ("", "t=,v1=", "v1=abc", f"t={int(time.time())}", "not-a-header"):
        assert _post_event(client, event, header=garbage).status_code == 400


def test_stale_timestamp_rejected(stripe_secret, client):
    """A captured delivery must not be replayable outside the tolerance window."""
    event = _account_updated("acct_whatever")
    raw = json.dumps(event).encode()
    stale = _sign(raw, timestamp=int(time.time()) - 600)
    assert _post_event(client, event, header=stale).status_code == 400
    future = _sign(raw, timestamp=int(time.time()) + 600)
    assert _post_event(client, event, header=future).status_code == 400


def test_rotated_secret_extra_v1_entries_accepted(stripe_secret, client):
    """During secret rotation Stripe sends several v1 signatures; one valid
    entry among invalid ones must verify."""
    _, worker, account_ref = _worker_with_captured_payment(client)
    event = _account_updated(account_ref)
    raw = json.dumps(event).encode()
    t = int(time.time())
    good = hmac.new(
        STRIPE_WEBHOOK_SECRET.encode(), f"{t}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    header = f"t={t},v1={'0' * 64},v1={good}"
    assert _post_event(client, event, header=header).status_code == 200
    assert client.get("/me/payout-account", headers=worker).json()["payouts_enabled"] is True


def test_duplicate_event_is_not_reprocessed(stripe_secret, client):
    _, worker, account_ref = _worker_with_captured_payment(client)
    event = _account_updated(account_ref, event_id="evt_dup_stripe")
    assert _post_event(client, event).json()["status"] == "processed"
    transfers_after_first = len(_fake_provider.transfers)
    assert _post_event(client, event).json()["status"] == "duplicate"
    assert len(_fake_provider.transfers) == transfers_after_first


def test_unknown_event_type_is_acknowledged(stripe_secret, client):
    event = {"id": "evt_other", "type": "balance.available", "data": {"object": {}}}
    resp = _post_event(client, event)
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


def test_unknown_account_is_acknowledged(stripe_secret, client):
    """An account we never created (e.g. made directly in the dashboard) is
    recorded and acked — Stripe must not retry it forever."""
    resp = _post_event(client, _account_updated("acct_not_ours"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "processed"


def test_malformed_event_rejected(stripe_secret, client):
    for payload in (b"not json", b'["a","list"]', b'{"type": "account.updated"}', b'{"id": 7, "type": "x"}'):
        header = _sign(payload)
        resp = client.post(
            "/webhooks/stripe",
            content=payload,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )
        assert resp.status_code == 400, payload


def test_unconfigured_secret_returns_503(no_stripe_secret, client):
    """Without a secret the route refuses everything loudly — registering the
    endpoint in Stripe before setting the env var must be visible, and the
    system stays correct on polling alone."""
    assert get_settings().stripe_webhook_secret is None
    resp = _post_event(client, _account_updated("acct_any"))
    assert resp.status_code == 503
