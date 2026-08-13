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

    # Nothing was paid out, so there was nothing to reverse.
    assert _fake_provider.transfer_reversals == []

    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def _paid_out_job(client, price_cents=10000):
    """Onboard the worker first so completion captures and pays out immediately."""
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    account = client.post("/me/payout-account", headers=worker).json()
    _enable_payouts(client, account["provider_account_ref"])
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": price_cents}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    return job, customer, worker


def test_full_refund_after_payout_reverses_the_transfer(client):
    """Refunding a paid-out payment must claw the worker's share back from their
    connected account — otherwise their ledger goes negative and the platform
    eats the loss."""
    job, customer, worker = _paid_out_job(client)
    assert client.get(f"/jobs/{job['id']}/payment", headers=worker).json()["status"] == "paid_out"

    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]
    refunded = client.post(f"/admin/payments/{payment_id}/refund", json={}, headers=admin).json()
    assert refunded["status"] == "refunded"
    assert refunded["refunded_cents"] == 10000

    # The worker's whole net came back off the payout rail.
    assert len(_fake_provider.transfer_reversals) == 1
    reversal = _fake_provider.transfer_reversals[0]
    assert reversal["amount_cents"] == 8500
    assert reversal["transfer_ref"] == _fake_provider.transfers[-1]["ref"]

    # Worker made whole, every account square.
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_partial_refunds_after_payout_reverse_proportionally(client):
    job, customer, worker = _paid_out_job(client)
    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]

    partial = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 4000}, headers=admin
    ).json()
    assert partial["status"] == "paid_out"
    assert partial["refunded_cents"] == 4000
    # Worker's 85% share of the partial refund comes back: 4000 * 0.85.
    assert _fake_provider.transfer_reversals[-1]["amount_cents"] == 3400
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0

    rest = client.post(f"/admin/payments/{payment_id}/refund", json={}, headers=admin).json()
    assert rest["status"] == "refunded"
    assert _fake_provider.transfer_reversals[-1]["amount_cents"] == 5100
    assert sum(r["amount_cents"] for r in _fake_provider.transfer_reversals) == 8500

    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_failed_reversal_leaves_the_refund_standing(client, monkeypatch):
    """A claw-back that fails must not undo the customer's refund.

    Rolling the refund back would erase a refund that already happened at the
    provider, and the retry would refund the customer a second time. The worker
    keeps their share, the platform absorbs it — the documented pre-existing
    behaviour — and the shortfall is visible as a negative worker balance rather
    than being silently papered over.
    """
    from app.modules.payments.provider import ProviderError

    job, customer, worker = _paid_out_job(client)
    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]

    def refuse(*args, **kwargs):
        raise ProviderError("insufficient balance on connected account")

    monkeypatch.setattr(_fake_provider, "reverse_transfer", refuse)
    resp = client.post(f"/admin/payments/{payment_id}/refund", json={}, headers=admin)
    assert resp.status_code == 200, resp.text

    payment = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()
    assert payment["status"] == "refunded"
    assert payment["refunded_cents"] == 10000
    assert _fake_provider.refunds, "the customer refund must have run"
    # The ledger still balances; the worker simply carries the un-clawed share.
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_a_failed_refund_cannot_orphan_a_reversal(client, monkeypatch):
    """A reversal must never outlive the refund that justified it.

    With the claw-back running first, a refund that failed afterwards left the
    reversal orphaned at the provider — the database rolled back knowing nothing
    about it — and an admin retrying at a different amount minted a second one
    under a different key. Measured before the fix: a failed 5000 refund clawed
    4250, then a 3000 retry clawed another 2550, taking 6800 from the worker to
    fund a 3000 refund.
    """
    from app.modules.payments.provider import ProviderError

    job, customer, worker = _paid_out_job(client)
    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]

    original_refund = _fake_provider.refund

    def outage(*args, **kwargs):
        raise ProviderError("simulated refund outage")

    monkeypatch.setattr(_fake_provider, "refund", outage)
    first = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 5000}, headers=admin
    )
    assert first.status_code == 502, first.text
    assert _fake_provider.transfer_reversals == [], "nothing may be clawed back for a refund that never happened"

    monkeypatch.setattr(_fake_provider, "refund", original_refund)
    retry = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 3000}, headers=admin
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["refunded_cents"] == 3000
    clawed = sum(r["amount_cents"] for r in _fake_provider.transfer_reversals)
    assert clawed == 3000 * 8500 // 10000, f"clawed {clawed} back for a 3000 refund"
    assert client.get("/admin/ledger/trial-balance", headers=admin).json()["balanced"] is True


def test_a_refund_too_small_to_split_is_accepted(client):
    """A refund whose worker share floors to zero is a legitimate refund.

    The ledger rejects zero-value entries, so building one unconditionally turned
    a 1-cent refund into an UnbalancedTransaction and a 500.
    """
    job, customer, worker = _paid_out_job(client)
    admin = make_admin(client)
    payment_id = client.get(f"/jobs/{job['id']}/payment", headers=customer).json()["id"]

    resp = client.post(
        f"/admin/payments/{payment_id}/refund", json={"amount_cents": 1}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["refunded_cents"] == 1
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


def test_payout_failure_does_not_undo_the_capture(client, monkeypatch):
    """A payout can fail for reasons unrelated to the job — an unsettled
    balance, a provider outage. The customer has still paid, so the capture and
    the debt owed to the worker must survive it."""
    from app.modules.payments.provider import ProviderError

    job, customer, worker = _accepted_job(client)
    account = client.post("/me/payout-account", headers=worker).json()
    _enable_payouts(client, account["provider_account_ref"])

    def refuse(*args, **kwargs):
        raise ProviderError("insufficient available funds", definitive=True)

    monkeypatch.setattr(_fake_provider, "transfer", refuse)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    resp = client.post(f"/jobs/{job['id']}/complete", headers=worker)
    assert resp.status_code == 200, "a failed payout must not fail the job"

    payment = client.get(f"/jobs/{job['id']}/payment", headers=worker).json()
    assert payment["status"] == "captured"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 8500


def test_definitive_payout_failure_advances_the_idempotency_key(client, monkeypatch):
    """Providers cache responses per idempotency key, errors included. Reusing a
    key after a definitive failure replays that failure until the cache expires,
    so the retry must carry a fresh one."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.modules.payments.models import Payment
    from app.modules.payments.provider import ProviderError

    job, customer, worker = _accepted_job(client)
    account = client.post("/me/payout-account", headers=worker).json()
    _enable_payouts(client, account["provider_account_ref"])

    keys: list[str] = []

    def refuse(*args, idempotency_key=None, **kwargs):
        keys.append(idempotency_key)
        raise ProviderError("insufficient available funds", definitive=True)

    monkeypatch.setattr(_fake_provider, "transfer", refuse)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    client.post("/me/payout-account", headers=worker)  # explicit retry

    assert len(keys) >= 2, "the payout should have been retried"
    assert keys[0] != keys[1], f"retry reused a poisoned key: {keys}"

    with SessionLocal() as db:
        payment = db.scalar(select(Payment).where(Payment.job_id == job["id"]))
        assert payment.payout_attempts >= 1


def test_ambiguous_payout_failure_keeps_the_same_key(client, monkeypatch):
    """When the outcome is unknown — a timeout — the retry must reuse the key so
    the provider deduplicates it rather than paying the worker twice."""
    from app.modules.payments.provider import ProviderError

    job, customer, worker = _accepted_job(client)
    account = client.post("/me/payout-account", headers=worker).json()
    _enable_payouts(client, account["provider_account_ref"])

    keys: list[str] = []

    def timeout(*args, idempotency_key=None, **kwargs):
        keys.append(idempotency_key)
        raise ProviderError("connection reset", definitive=False)

    monkeypatch.setattr(_fake_provider, "transfer", timeout)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)
    client.post("/me/payout-account", headers=worker)

    assert len(keys) >= 2
    assert keys[0] == keys[1], f"an unknown outcome must reuse its key: {keys}"
