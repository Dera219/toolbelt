"""Shared rig for the reconciliation tests.

Both the offline suite (test_reconcile.py) and the Stripe test-mode suite
(test_stripe_provider.py) have to build the same two states — a journal row for
a call that landed, and one for a call that never did — and they must build them
the same way or the two suites are testing different things. Hence one file.
"""

import hashlib
import hmac
import json
from datetime import timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.modules.identity.models import utcnow
from app.modules.payments import reconcile
from app.modules.payments.journal import _fingerprint
from app.modules.payments.models import (
    BillingProfile,
    Payment,
    ProviderCall,
    ProviderCallStatus,
)
from tests.conftest import customer_with_pm, make_worker, post_job

# The internal fake-provider webhook secret, matching config._DEV_WEBHOOK_SECRET.
WEBHOOK_SECRET = b"dev-webhook-secret-0123456789abcdef"


def enable_payouts(client, account_ref: str, event_id: str = "evt_recon") -> None:
    raw = json.dumps(
        {
            "id": event_id,
            "type": "account.updated",
            "data": {"account_ref": account_ref, "payouts_enabled": True},
        }
    ).encode()
    signature = hmac.new(WEBHOOK_SECRET, raw, hashlib.sha256).hexdigest()
    resp = client.post(
        "/webhooks/payments",
        content=raw,
        headers={"X-Webhook-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200, resp.text


def accepted_job(client, price_cents: int = 10000, onboard_worker: bool = False):
    """A job with an authorized payment.

    Onboarding the worker makes completion capture *and* pay out, which is what
    the transfer and reversal paths need.
    """
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    if onboard_worker:
        account = client.post("/me/payout-account", headers=worker).json()
        enable_payouts(client, account["provider_account_ref"])
    job = post_job(client, customer)
    offer = client.post(
        f"/jobs/{job['id']}/offers", json={"price_cents": price_cents}, headers=worker
    ).json()
    assert client.post(f"/offers/{offer['id']}/accept", headers=customer).status_code == 200
    return job, customer, worker


def complete_job(client, job, worker) -> None:
    client.post(f"/jobs/{job['id']}/start", headers=worker)
    client.post(f"/jobs/{job['id']}/complete", headers=worker)


def payment_for(job_id: int) -> Payment:
    with SessionLocal() as db:
        payment = db.scalar(select(Payment).where(Payment.job_id == job_id))
        assert payment is not None, f"no payment for job {job_id}"
        return payment


def journal_row(key: str) -> ProviderCall | None:
    with SessionLocal() as db:
        return db.scalar(select(ProviderCall).where(ProviderCall.idempotency_key == key))


def journal_keys() -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(ProviderCall.idempotency_key)))


def reopen(key: str, *, age_minutes: int = 60) -> None:
    """Reproduce the crash the sweeper exists for.

    The provider call really happened — the object is at the provider — and the
    completion write never landed, so the row still reads `pending` with no
    reference. Backdated past the grace period so a sweep will judge it.
    """
    with SessionLocal() as db:
        row = db.scalar(select(ProviderCall).where(ProviderCall.idempotency_key == key))
        assert row is not None, f"no journal row {key!r}; have {journal_keys()}"
        row.status = ProviderCallStatus.PENDING
        row.provider_ref = None
        row.error = None
        row.completed_at = None
        row.created_at = utcnow() - timedelta(minutes=age_minutes)
        db.commit()


def pending(
    key: str, operation: str, payment_id: int | None, params: dict, *, age_minutes: int = 60
) -> None:
    """A `pending` row for a call that never reached the provider.

    The fingerprint is built from the parameters the call *would* have carried,
    which is exactly what the journal writes before it dials out.
    """
    with SessionLocal() as db:
        db.add(
            ProviderCall(
                idempotency_key=key,
                operation=operation,
                payment_id=payment_id,
                request_fingerprint=_fingerprint(params),
                status=ProviderCallStatus.PENDING,
                created_at=utcnow() - timedelta(minutes=age_minutes),
            )
        )
        db.commit()


def sweep(*, dry_run: bool = False, **kwargs) -> reconcile.ReconciliationReport:
    with SessionLocal() as db:
        return reconcile.reconcile_pending_calls(db, dry_run=dry_run, **kwargs)


def only(report: reconcile.ReconciliationReport) -> reconcile.Outcome:
    assert report.scanned == 1, [
        (o.key, o.resolution.value, o.detail) for o in report.outcomes
    ]
    return report.outcomes[0]


def authorize_key(job_id: int, amount_cents: int) -> str:
    """The key authorize_for_job built, rebuilt from the same helper it used.

    Spelling it out by hand here would let the two drift, and a test that
    reconciles a key nothing ever wrote proves nothing.
    """
    from app.modules.payments.service import authorize_idempotency_key

    with SessionLocal() as db:
        customer_id = db.scalar(select(Payment.customer_id).where(Payment.job_id == job_id))
        profile = db.get(BillingProfile, customer_id)
        return authorize_idempotency_key(job_id, amount_cents, profile.default_payment_method_ref)
