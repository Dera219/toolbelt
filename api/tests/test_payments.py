import hashlib
import hmac
import json

from app.modules.payments.provider import _fake_provider
from tests.conftest import customer_with_pm, login, make_admin, make_worker, post_job, register

WEBHOOK_SECRET = "dev-webhook-secret-0123456789abcdef"


def _signed_webhook(client, event: dict):
    raw = json.dumps(event).encode()
    signature = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/payments",
        content=raw,
        headers={"X-Webhook-Signature": signature, "Content-Type": "application/json"},
    )


def _enable_payouts(client, account_ref: str, event_id: str = "evt_1"):
    resp = _signed_webhook(
        client,
        {
            "id": event_id,
            "type": "account.updated",
            "data": {"account_ref": account_ref, "payouts_enabled": True},
        },
    )
    assert resp.status_code == 200, resp.text
    return resp


def _accepted_job(client, price_cents=10000):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": price_cents}, headers=worker
    ).json()
    accept = client.post(f"/offers/{offer['id']}/accept", headers=customer)
    assert accept.status_code == 200, accept.text
    return job, customer, worker


def test_full_money_loop_with_onboarded_worker(client):
    """authorize on accept → capture on complete → instant payout, 15% fee retained."""
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    account = client.post("/me/payout-account", headers=worker).json()
    assert account["payouts_enabled"] is False
    assert "onboarding.fake" in account["onboarding_url"]
    _enable_payouts(client, account["provider_account_ref"])

    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)

    payment = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()
    assert payment["status"] == "authorized"
    assert payment["amount_cents"] == 10000
    assert payment["platform_fee_cents"] == 1500
    assert payment["worker_net_cents"] == 8500
    assert _fake_provider.authorizations[-1]["amount_cents"] == 10000

    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=customer)

    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "paid_out"
    assert _fake_provider.transfers[-1]["amount_cents"] == 8500
    # Worker balance is zero after payout; the fee is the platform's.
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0

    admin = make_admin(client)
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_capture_holds_balance_until_onboarding(client):
    """No payout account yet → funds sit on the worker's ledger balance; onboarding
    webhook flushes the pending payout."""
    job, customer, worker = _accepted_job(client)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)

    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "captured"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500

    account = client.post("/me/payout-account", headers=worker).json()
    _enable_payouts(client, account["provider_account_ref"])

    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "paid_out"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0


def test_declined_card_rolls_back_acceptance(client):
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()

    _fake_provider.fail_next_authorize = True
    resp = client.post(f"/offers/{offer['id']}/accept", headers=customer)
    assert resp.status_code == 402

    # Nothing moved: job still open, offer still pending, retry succeeds.
    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "open"
    resp = client.post(f"/offers/{offer['id']}/accept", headers=customer)
    assert resp.status_code == 200
    assert client.get(f"/jobs/{job['id']}", headers=customer).json()["status"] == "assigned"


def test_no_payment_method_blocks_acceptance(client):
    register(client, "customer@example.com")
    customer = login(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
    ).json()
    resp = client.post(f"/offers/{offer['id']}/accept", headers=customer)
    assert resp.status_code == 402


def test_cancel_releases_authorization(client):
    job, customer, worker = _accepted_job(client)
    client.post(f"/jobs/{job['id']}/cancel", headers=customer)

    payment = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()
    assert payment["status"] == "released"
    assert len(_fake_provider.releases) == 1
    # No money moved → empty ledger.
    admin = make_admin(client)
    balance = client.get("/admin/ledger/trial-balance", headers=admin).json()
    assert balance == {"total_cents": 0, "balanced": True}


def test_partial_then_full_refund(client):
    job, customer, worker = _accepted_job(client)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)

    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]

    partial = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 4000}, headers=admin
    ).json()
    assert partial["refunded_cents"] == 4000
    assert partial["status"] == "captured"
    # Worker gave back their proportional share of the partial refund: 4000 * 0.85.
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500 - 3400

    rest = client.post(f"/admin/payments/{payment_id}/refund", json={}, headers=admin).json()
    assert rest["refunded_cents"] == 10000
    assert rest["status"] == "refunded"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0

    over = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 1}, headers=admin
    )
    assert over.status_code == 409  # fully refunded payments are closed

    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_webhook_signature_and_idempotency(client):
    worker = make_worker(client, "worker@example.com")
    account = client.post("/me/payout-account", headers=worker).json()

    event = {
        "id": "evt_dup",
        "type": "account.updated",
        "data": {"account_ref": account["provider_account_ref"], "payouts_enabled": True},
    }
    raw = json.dumps(event).encode()
    bad = client.post(
        "/webhooks/payments", content=raw,
        headers={"X-Webhook-Signature": "forged", "Content-Type": "application/json"},
    )
    assert bad.status_code == 400

    assert _signed_webhook(client, event).json()["status"] == "processed"
    assert _signed_webhook(client, event).json()["status"] == "duplicate"


def test_payment_visibility_is_parties_only(client):
    job, customer, worker = _accepted_job(client)
    register(client, "stranger@example.com")
    stranger = login(client, "stranger@example.com")
    assert client.get(f"/jobs/{job['id']}/payment", headers=stranger).status_code == 403
