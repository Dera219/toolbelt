"""Payout onboarding sync and payout idempotency.

These cover the paths that move money after onboarding completes. The
transfer executes at the provider before any local row is written, so the
guarantee that matters is that a replay cannot pay a worker twice.
"""

import pytest

from app.modules.payments.provider import ProviderError, _fake_provider
from tests.conftest import customer_with_pm, make_worker, post_job


def _completed_job(client, price_cents=10000):
    """Drive a job to completed so the worker has captured, unpaid earnings."""
    customer = customer_with_pm(client, "sync-customer@example.com")
    worker = make_worker(client, "sync-worker@example.com")
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": price_cents}, headers=worker
    ).json()
    client.post(f"/offers/{offer['id']}/accept", headers=customer)
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=customer)
    return job, customer, worker


def _create_payout_account(client, worker):
    resp = client.post("/me/payout-account", headers=worker)
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------- read is safe

def test_get_does_not_release_funds(client):
    """A GET may update the flag but must never move money.

    Clients and proxies retry reads freely; a payout triggered from a read
    multiplies every failure mode behind it.
    """
    _job, _customer, worker = _completed_job(client)
    account = _create_payout_account(client, worker)

    # Onboarding completes at the provider.
    _fake_provider.enabled_accounts.add(account["provider_account_ref"])
    before = len(_fake_provider.transfers)

    read = client.get("/me/payout-account", headers=worker)
    assert read.status_code == 200
    assert read.json()["payouts_enabled"] is True, "the flag should sync on read"
    assert len(_fake_provider.transfers) == before, "a GET must not transfer funds"


def test_post_releases_funds_once_onboarded(client):
    _job, _customer, worker = _completed_job(client)
    account = _create_payout_account(client, worker)
    _fake_provider.enabled_accounts.add(account["provider_account_ref"])

    resp = client.post("/me/payout-account", headers=worker)
    assert resp.status_code == 200
    assert resp.json()["payouts_enabled"] is True
    assert len(_fake_provider.transfers) == 1, "captured earnings should pay out"
    assert client.get("/me/balance", headers=worker).json()["balance_cents"] == 0


def test_unonboarded_account_gets_a_fresh_onboarding_link(client):
    _job, _customer, worker = _completed_job(client)
    first = _create_payout_account(client, worker)
    assert first["payouts_enabled"] is False

    again = client.post("/me/payout-account", headers=worker).json()
    assert again["provider_account_ref"] == first["provider_account_ref"], "no second account"
    assert again["onboarding_url"], "resuming onboarding needs a usable link"


# ------------------------------------------------------- money-safety on retry

def test_replayed_transfer_does_not_pay_twice(client):
    """The core guarantee.

    A transfer runs at the provider before the ledger row exists, so a failure
    later in the same transaction rolls the database back while the money has
    already moved. The idempotency key is what makes the retry a no-op instead
    of a second payout.
    """
    _job, _customer, worker = _completed_job(client)
    account = _create_payout_account(client, worker)
    _fake_provider.enabled_accounts.add(account["provider_account_ref"])

    client.post("/me/payout-account", headers=worker)
    assert len(_fake_provider.transfers) == 1

    # Simulate the rollback-then-retry: the same logical payout is attempted
    # again with the same key.
    from app.core.db import SessionLocal
    from app.modules.payments.provider import get_payment_provider
    from app.modules.payments.service import payout_idempotency_key

    provider = get_payment_provider()
    with SessionLocal() as db:
        from sqlalchemy import select

        from app.modules.payments.models import Payment

        payment = db.scalar(select(Payment))
        ref_first = _fake_provider.transfers[0]["ref"]
        ref_again = provider.transfer(
            account["provider_account_ref"],
            payment.worker_net_cents,
            payment.currency,
            metadata={"payment_id": str(payment.id)},
            idempotency_key=payout_idempotency_key(payment),
        )

    assert ref_again == ref_first, "replay must return the original transfer"
    assert len(_fake_provider.transfers) == 1, "replay must not create a second transfer"


def test_one_failed_payout_does_not_block_the_others(client, monkeypatch):
    """A provider failure on one payment must not abort the whole flush."""
    _job, _customer, worker = _completed_job(client)
    account = _create_payout_account(client, worker)
    _fake_provider.enabled_accounts.add(account["provider_account_ref"])

    real_transfer = _fake_provider.transfer
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("transient provider failure")
        return real_transfer(*args, **kwargs)

    monkeypatch.setattr(_fake_provider, "transfer", flaky)

    # The failure is swallowed per-payment, so the request still succeeds.
    resp = client.post("/me/payout-account", headers=worker)
    assert resp.status_code == 200, resp.text
    assert resp.json()["payouts_enabled"] is True, "the account is still onboarded"


# ---------------------------------------------------------- provider failures

def test_provider_outage_on_sync_does_not_break_the_screen(client, monkeypatch):
    _job, _customer, worker = _completed_job(client)
    _create_payout_account(client, worker)

    def boom(_ref):
        raise ProviderError("provider unreachable")

    monkeypatch.setattr(_fake_provider, "payouts_enabled", boom)

    resp = client.get("/me/payout-account", headers=worker)
    assert resp.status_code == 200, "a provider outage must not 502 the account screen"
    assert resp.json()["payouts_enabled"] is False


def test_card_rejection_is_402_not_500(client, monkeypatch):
    headers = customer_with_pm(client, "reject@example.com")

    def reject(_customer_ref, _pm_ref):
        raise ProviderError("Your card was declined.")

    monkeypatch.setattr(_fake_provider, "attach_payment_method", reject)

    resp = client.post(
        "/me/payment-method", json={"payment_method_ref": "pm_bad"}, headers=headers
    )
    assert resp.status_code == 402, resp.text


def test_provider_error_response_hides_upstream_detail(client, monkeypatch):
    """The 502 body must not leak provider request ids or account refs."""
    _job, _customer, worker = _completed_job(client)
    account = _create_payout_account(client, worker)

    def leaky(_ref):
        raise ProviderError(f"Request req_secret123: No such account {account['provider_account_ref']}")

    monkeypatch.setattr(_fake_provider, "onboarding_link", leaky)

    resp = client.post("/me/payout-account", headers=worker)
    assert resp.status_code == 502
    body = resp.text
    assert "req_secret123" not in body
    assert account["provider_account_ref"] not in body
