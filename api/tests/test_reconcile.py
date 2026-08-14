"""The reconciliation sweeper: what it can resolve, what it refuses to, and what
it is never allowed to do.

Every test here works the same way, because it is the way the defect actually
happens. A real money flow is driven through the API so the provider genuinely
holds the object, and then the journal row is reopened — status back to
`pending`, reference cleared, timestamp backdated past the grace period. That is
exactly the state a crash between the provider call and the completion write
leaves behind: the money moved, the record does not know it.

The other half of the suite builds the opposite state — a `pending` row for a
call that never reached the provider — because telling those two apart is the
sweeper's entire job.
"""

import pytest
from sqlalchemy import delete, select

from app.core.db import SessionLocal
from app.modules.payments import reconcile
from app.modules.payments.models import (
    BillingProfile,
    LedgerEntry,
    Payment,
    PaymentStatus,
    ProviderCallStatus,
)
from app.modules.payments.provider import AuthorizationState, _fake_provider
from app.modules.payments.reconcile import MoneyMovementRefused, Resolution
from tests.conftest import customer_with_pm, login, make_admin, make_worker, post_job, register

# Shared with the Stripe test-mode suite on purpose: both have to build the same
# two states — a call that landed and a call that never did — and building them
# two different ways would mean the two suites are testing two different things.
from tests.reconcile_helpers import (
    accepted_job,
    authorize_key,
    complete_job,
    journal_keys,
    journal_row,
    only,
    payment_for,
    pending,
    reopen,
    sweep,
)


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------


def test_capture_resolves_to_succeeded_with_the_real_charge_reference(client):
    """The core case. The customer was charged, the record never learned it."""
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == payment.provider_charge_ref
    assert outcome.written is True
    row = journal_row(key)
    assert row.status is ProviderCallStatus.SUCCEEDED
    assert row.provider_ref == payment.provider_charge_ref
    # Provenance: a reference the sweeper inferred must be distinguishable from
    # one the live call returned.
    assert "reconciled" in row.error
    assert outcome.discrepancy is None, "the books already matched; nothing to escalate"


def test_a_capture_that_never_reached_the_provider_resolves_to_failed(client):
    """'Nothing happened', asserted rather than assumed.

    The authorization is still an uncaptured hold, which is positive evidence
    that the capture never landed — not the absence of evidence that it did.
    """
    job, customer, worker = accepted_job(client)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"
    pending(key, "capture", payment.id, {"auth_ref": payment.provider_auth_ref})

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert outcome.provider_ref is None
    assert "still an uncaptured hold" in outcome.detail
    assert journal_row(key).status is ProviderCallStatus.FAILED
    assert payment_for(job["id"]).status is PaymentStatus.AUTHORIZED, "the payment is untouched"


def test_a_capture_the_provider_took_but_our_books_missed_is_reported_not_repaired(client):
    """The discrepancy path. The sweeper says it loudly and fixes nothing."""
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"

    # The completion transaction rolled back after the capture: the charge
    # happened, and the payment, its charge reference and the ledger entry all
    # went back to how they were.
    with SessionLocal() as db:
        row = db.get(Payment, payment.id)
        row.status = PaymentStatus.AUTHORIZED
        row.provider_charge_ref = None
        db.execute(delete(LedgerEntry).where(LedgerEntry.txn_key == f"capture:{payment.id}"))
        db.commit()
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.discrepancy is not None
    assert "MONEY TAKEN, NOT RECORDED" in outcome.discrepancy
    assert f"capture:{payment.id}" in outcome.discrepancy, "names the missing ledger txn"
    assert str(payment.id) in outcome.discrepancy

    after = payment_for(job["id"])
    assert after.status is PaymentStatus.AUTHORIZED, "the sweeper must not repair money state"
    assert after.provider_charge_ref is None
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(LedgerEntry.id).where(LedgerEntry.txn_key == f"capture:{payment.id}")
            )
            is None
        ), "the sweeper must not write ledger entries"


# --------------------------------------------------------------------------
# release
# --------------------------------------------------------------------------


def test_release_resolves_to_succeeded_against_the_cancelled_authorization(client):
    job, customer, worker = accepted_job(client)
    client.post(f"/jobs/{job['id']}/cancel", headers=customer)
    payment = payment_for(job["id"])
    key = f"release-call:{payment.id}"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    # A cancellation creates no object of its own, so the journal records the
    # authorization it voided — the same reference the live call returns.
    assert outcome.provider_ref == payment.provider_auth_ref
    assert outcome.discrepancy is None


def test_a_release_that_never_landed_leaves_the_customer_held_and_says_so(client):
    """The finding worth the whole feature: we think the job is cancelled and the
    customer's card is still frozen."""
    job, customer, worker = accepted_job(client)
    client.post(f"/jobs/{job['id']}/cancel", headers=customer)
    payment = payment_for(job["id"])

    # The cancel never actually reached the provider: put the hold back.
    _fake_provider._authorization(payment.provider_auth_ref)["state"] = AuthorizationState.HELD
    key = f"release-call:{payment.id}"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert outcome.discrepancy is not None
    assert "CUSTOMER STILL HELD" in outcome.discrepancy


# --------------------------------------------------------------------------
# authorize — the one with a genuine limit
# --------------------------------------------------------------------------


def test_authorize_resolves_from_the_payment_the_call_created(client):
    """No listing needed: the payment the authorize produced points straight at
    the object, and one retrieve settles it."""
    job, customer, worker = accepted_job(client)
    payment = payment_for(job["id"])
    key = authorize_key(job["id"], 10000)
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == payment.provider_auth_ref
    assert outcome.payment_id is None, "an authorize row never carries a payment id"
    assert outcome.discrepancy is None


def test_an_authorization_with_no_payment_row_is_found_and_reported_as_orphaned(client):
    """The hold nobody will ever capture or release.

    The payment row is gone, so the reference is gone with it; the provider is
    asked for the customer's recent authorizations and the job metadata picks
    ours out. Resolving it is not enough — a hold with no payment behind it
    freezes the customer's money until the provider expires it, so it escalates.
    """
    job, customer, worker = accepted_job(client)
    payment = payment_for(job["id"])
    key = authorize_key(job["id"], 10000)
    with SessionLocal() as db:
        db.execute(delete(Payment).where(Payment.id == payment.id))
        db.commit()
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == payment.provider_auth_ref
    assert outcome.discrepancy is not None
    assert "ORPHANED HOLD" in outcome.discrepancy


def test_two_jobs_at_the_same_price_do_not_get_each_others_holds(client):
    """The fingerprint alone is not enough for authorize, and this is why.

    Every field the authorize fingerprint covers comes from local state except
    the payment method, which the candidate supplies — so one customer's two
    jobs at the same price hash identically. Without the job metadata as a
    discriminator the sweep sees two matching holds and either misattributes one
    or raises a false DUPLICATE HOLDS alarm.

    Both payment rows are deleted and both journal rows reopened, which is what
    strips away every other discriminator: with a payment present the sweeper
    short-circuits on the known reference, and with the sibling row still
    `succeeded` its hold is already claimed. Neither crutch is available here, so
    only the job metadata separates the two.
    """
    customer = customer_with_pm(client, "customer@example.com")
    worker = make_worker(client, "worker@example.com")
    jobs = []
    for _ in range(2):
        job = post_job(client, customer)
        offer = client.post(
            f"/jobs/{job['id']}/offers", json={"price_cents": 10000}, headers=worker
        ).json()
        assert client.post(f"/offers/{offer['id']}/accept", headers=customer).status_code == 200
        jobs.append(job)

    holds = {job["id"]: payment_for(job["id"]).provider_auth_ref for job in jobs}
    assert len(set(holds.values())) == 2
    keys = {job["id"]: authorize_key(job["id"], 10000) for job in jobs}

    with SessionLocal() as db:
        db.execute(delete(Payment))
        db.commit()
    for key in keys.values():
        reopen(key)

    report = sweep()

    assert report.scanned == 2
    resolved = {o.key: o for o in report.outcomes}
    for job_id, key in keys.items():
        outcome = resolved[key]
        assert outcome.resolution is Resolution.SUCCEEDED, outcome.detail
        assert outcome.provider_ref == holds[job_id], (
            "each call must be matched to its own job's hold — the other job's hold is "
            "the same amount on the same customer with the same card"
        )
        assert "ORPHANED HOLD" in outcome.discrepancy


def test_an_authorize_that_never_reached_the_provider_resolves_to_failed(client):
    """A job that was never accepted: no payment, and no intent at the provider
    for that job. Absence here is real absence, because the lookup is by the
    customer we know and the provider's list read is strongly consistent."""
    customer = customer_with_pm(client, "customer@example.com")
    make_worker(client, "worker@example.com")
    job = post_job(client, customer)
    with SessionLocal() as db:
        profile = db.scalar(select(BillingProfile))
        pm = profile.default_payment_method_ref
        customer_ref = profile.provider_customer_ref
    key = f"authorize:{job['id']}:7000:{pm[-14:]}"
    pending(
        key,
        "authorize",
        None,
        {
            "amount": 7000,
            "currency": "USD",
            "customer": customer_ref,
            "payment_method": pm,
            "job_id": job["id"],
        },
    )

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert "never reached" in outcome.detail
    assert journal_row(key).status is ProviderCallStatus.FAILED


def test_an_authorize_with_no_billing_profile_is_reported_unknown_not_guessed(client):
    """The stated limit, asserted.

    An authorize's journal row carries no payment id — by construction, the call
    is what justifies creating one — so the only route to the provider is
    job → customer → billing profile → provider customer. Break that chain and
    there is no handle at all. The sweeper says 'unknown' rather than inventing
    a resolution, and the row stays pending.
    """
    job, customer, worker = accepted_job(client)
    key = authorize_key(job["id"], 10000)
    reopen(key)
    with SessionLocal() as db:
        payment = db.scalar(select(Payment).where(Payment.job_id == job["id"]))
        customer_id = payment.customer_id
        db.execute(delete(Payment).where(Payment.id == payment.id))
        db.execute(delete(BillingProfile).where(BillingProfile.user_id == customer_id))
        db.commit()

    outcome = only(sweep())

    assert outcome.resolution is Resolution.UNKNOWN
    assert "no billing profile" in outcome.detail
    assert "cannot be resolved" in outcome.detail
    assert journal_row(key).status is ProviderCallStatus.PENDING, "unknown never writes"


def test_an_unparseable_authorize_key_is_reported_unknown(client):
    """The other end of the same limit: a key that does not carry a job and an
    amount describes a call nothing here can look up."""
    pending("authorize:legacy-format", "authorize", None, {"amount": 1})

    outcome = only(sweep())

    assert outcome.resolution is Resolution.UNKNOWN
    assert "not in the authorize format" in outcome.detail


# --------------------------------------------------------------------------
# refund
# --------------------------------------------------------------------------


def test_refund_resolves_to_succeeded_with_the_real_refund_reference(client):
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    admin = make_admin(client)
    resp = client.post(
        f"/admin/payments/{payment.id}/refund", json={"amount_cents": 4000}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    refund_ref = _fake_provider.refunds[-1]["ref"]
    key = f"refund-call:{payment.id}:0"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == refund_ref
    assert outcome.discrepancy is None, "the refund ledger entry exists, so nothing to escalate"
    assert journal_row(key).provider_ref == refund_ref


def test_a_refund_that_never_reached_the_provider_resolves_to_failed(client):
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"refund-call:{payment.id}:0"
    pending(
        key,
        "refund",
        payment.id,
        {"charge": payment.provider_charge_ref, "amount": 4321},
    )

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert "the refund never landed" in outcome.detail
    assert payment_for(job["id"]).refunded_cents == 0, "the sweeper must not refund anyone"


def test_an_absent_refund_on_an_inferred_charge_is_unknown_not_failed(client):
    """Absence is only evidence if you were looking in the right place.

    The payment's charge reference is gone, so it has to be inferred from the
    authorization. Finding no matching refund on an inferred charge is one
    inference deep — not enough to tell an operator the customer was not
    refunded, which is exactly the lie this module exists to avoid.
    """
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"refund-call:{payment.id}:0"
    pending(key, "refund", payment.id, {"charge": payment.provider_charge_ref, "amount": 4321})
    with SessionLocal() as db:
        db.get(Payment, payment.id).provider_charge_ref = None
        db.commit()

    outcome = only(sweep())

    assert outcome.resolution is Resolution.UNKNOWN
    assert "not proof" in outcome.detail
    assert journal_row(key).status is ProviderCallStatus.PENDING


def test_a_refund_the_provider_made_and_the_ledger_missed_is_escalated(client):
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    admin = make_admin(client)
    client.post(
        f"/admin/payments/{payment.id}/refund", json={"amount_cents": 4000}, headers=admin
    )
    with SessionLocal() as db:
        db.execute(
            delete(LedgerEntry).where(LedgerEntry.txn_key == f"refund:{payment.id}:4000")
        )
        row = db.get(Payment, payment.id)
        row.refunded_cents = 0
        db.commit()
    reopen(f"refund-call:{payment.id}:0")

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert "REFUND NOT RECORDED" in outcome.discrepancy
    assert f"refund:{payment.id}:4000" in outcome.discrepancy
    assert payment_for(job["id"]).refunded_cents == 0, "reported, not repaired"


# --------------------------------------------------------------------------
# transfer
# --------------------------------------------------------------------------


def test_transfer_resolves_to_succeeded_with_the_real_transfer_reference(client):
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    assert payment.status is PaymentStatus.PAID_OUT
    key = f"payout:{payment.id}#0"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == payment.provider_payout_ref
    assert outcome.discrepancy is None


def test_a_transfer_that_never_reached_the_provider_resolves_to_failed(client):
    """Payouts are off for this worker, so completion captures without paying
    out and no transfer exists — the state a payout crash leaves behind."""
    job, customer, worker = accepted_job(client)
    client.post("/me/payout-account", headers=worker)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    assert payment.status is PaymentStatus.CAPTURED
    from app.modules.payments.models import PayoutAccount

    with SessionLocal() as db:
        account = db.get(PayoutAccount, payment.worker_id)
        account_ref = account.provider_account_ref
    key = f"payout:{payment.id}#0"
    pending(
        key,
        "transfer",
        payment.id,
        {
            "destination": account_ref,
            "amount": payment.worker_net_cents,
            "currency": payment.currency,
        },
    )

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert "the payout never landed" in outcome.detail
    assert payment_for(job["id"]).status is PaymentStatus.CAPTURED


def test_two_transfers_for_one_payment_are_reported_as_a_double_payout(client):
    """The defect the journal exists to prevent, caught by the sweeper.

    Two transfers carrying the same payment_id means the worker was paid twice.
    Neither can be attributed to this row, so it stays pending — and the finding
    is escalated rather than swallowed by a tidy resolution.
    """
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    original = _fake_provider.transfers[-1]
    _fake_provider.transfers.append({**original, "ref": "tr_fake_duplicate"})
    reopen(f"payout:{payment.id}#0")

    outcome = only(sweep())

    assert outcome.resolution is Resolution.UNKNOWN
    assert "DOUBLE PAYOUT" in outcome.discrepancy
    assert "tr_fake_duplicate" in outcome.discrepancy
    assert journal_row(f"payout:{payment.id}#0").status is ProviderCallStatus.PENDING


# --------------------------------------------------------------------------
# reverse_transfer
# --------------------------------------------------------------------------


def test_reverse_transfer_resolves_to_succeeded_with_the_real_reversal(client):
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    admin = make_admin(client)
    resp = client.post(f"/admin/payments/{payment.id}/refund", json={}, headers=admin)
    assert resp.status_code == 200, resp.text
    reversal_ref = _fake_provider.transfer_reversals[-1]["ref"]
    key = f"reverse:{payment.id}:10000"
    reopen(key)

    outcome = only(sweep())

    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == reversal_ref
    assert outcome.discrepancy is None


def test_a_reversal_that_never_reached_the_provider_resolves_to_failed(client):
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"reverse:{payment.id}:5000"
    pending(
        key,
        "reverse_transfer",
        payment.id,
        {"transfer": payment.provider_payout_ref, "amount": 4250},
    )

    outcome = only(sweep())

    assert outcome.resolution is Resolution.FAILED
    assert "the claw-back never landed" in outcome.detail
    assert _fake_provider.transfer_reversals == [], "the sweeper must not claw anything back"


# --------------------------------------------------------------------------
# Dry run, grace period, and rows that are already settled
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing_while_reporting_everything(client):
    """The default everywhere it is exposed. A first run against production has
    to be incapable of changing anything, and still show what it would do."""
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"
    reopen(key)

    report = sweep(dry_run=True)
    outcome = only(report)

    assert report.dry_run is True
    assert outcome.resolution is Resolution.SUCCEEDED
    assert outcome.provider_ref == payment.provider_charge_ref
    assert outcome.written is False
    row = journal_row(key)
    assert row.status is ProviderCallStatus.PENDING, "a dry run must not write"
    assert row.provider_ref is None
    assert row.completed_at is None

    # And the applied run reaches the identical verdict.
    applied = only(sweep(dry_run=False))
    assert applied.resolution is outcome.resolution
    assert applied.provider_ref == outcome.provider_ref
    assert applied.written is True


def test_rows_inside_the_grace_period_are_counted_but_not_judged(client):
    """A row written a minute ago is a call that is probably still in flight.
    Judging it races the live attempt, and losing that race records 'nothing
    moved' over money that lands a second later."""
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"
    reopen(key, age_minutes=1)

    report = sweep()

    assert report.scanned == 0
    assert report.in_grace_period == 1
    assert journal_row(key).status is ProviderCallStatus.PENDING

    # Past the grace period the same row resolves.
    assert only(sweep(older_than_minutes=0)).resolution is Resolution.SUCCEEDED


def test_settled_rows_are_never_reopened(client):
    """The sweeper reads `pending` only. A succeeded row already holds the
    truth, and rewriting it from a later provider read could only make it worse."""
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    before = {r: journal_row(r).provider_ref for r in journal_keys()}
    assert before, "the flow must have journalled something"

    report = sweep()

    assert report.scanned == 0
    assert report.in_grace_period == 0
    assert {r: journal_row(r).provider_ref for r in journal_keys()} == before


# --------------------------------------------------------------------------
# The prohibition
# --------------------------------------------------------------------------


class MoneySpy:
    """A provider that answers reads with nothing and detonates on any money call.

    The point is not that today's code happens not to call these. It is that a
    future edit which does will fail a test instead of moving a customer's money
    from a cron job at 3am.
    """

    def __init__(self) -> None:
        self.reads = 0

    def _forbidden(self, name):
        def boom(*args, **kwargs):
            raise AssertionError(
                f"reconciliation called provider.{name}() — the sweeper closes the "
                "knowledge gap, never the money gap"
            )

        return boom

    def __getattr__(self, name: str):
        if name in {
            "authorize",
            "capture",
            "release",
            "refund",
            "transfer",
            "reverse_transfer",
            "create_customer",
            "attach_payment_method",
            "create_payout_account",
        }:
            return self._forbidden(name)
        raise AttributeError(name)

    def lookup_authorization(self, auth_ref):
        self.reads += 1
        return None

    def lookup_authorizations_for_customer(self, customer_ref, *, since):
        self.reads += 1
        return []

    def lookup_refunds(self, charge_ref):
        self.reads += 1
        return []

    def lookup_transfers_to(self, account_ref, *, since):
        self.reads += 1
        return []

    def lookup_transfer_reversals(self, transfer_ref):
        self.reads += 1
        return []


def test_the_sweeper_never_calls_a_money_moving_provider_method(client, monkeypatch):
    """Every operation, swept at once, against a provider that raises on any
    mutation."""
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    admin = make_admin(client)
    client.post(f"/admin/payments/{payment.id}/refund", json={}, headers=admin)
    for key in journal_keys():
        reopen(key)
    # Plus a release, which the paid-out flow never produces.
    pending(
        f"release-call:{payment.id}", "release", payment.id,
        {"auth_ref": payment.provider_auth_ref},
    )

    spy = MoneySpy()
    monkeypatch.setattr(reconcile, "get_payment_provider", lambda: spy)
    report = sweep()

    assert report.scanned >= 5, "every operation in the flow must have been swept"
    assert spy.reads > 0, "the sweep must actually have asked the provider something"
    assert {o.operation for o in report.outcomes} >= {
        "authorize", "capture", "release", "transfer", "refund", "reverse_transfer"
    }


def test_the_read_only_guard_refuses_every_money_method():
    """The structural half of the prohibition, tested directly. A policy in a
    docstring is one a future edit violates silently; this one raises."""
    guarded = reconcile._ReadOnlyProvider(_fake_provider)

    for method in ("authorize", "capture", "release", "refund", "transfer", "reverse_transfer"):
        with pytest.raises(MoneyMovementRefused) as raised:
            getattr(guarded, method)
        assert method in str(raised.value)

    # And the reads it exists to allow still work.
    assert guarded.lookup_refunds("ch_nothing") == []


# --------------------------------------------------------------------------
# The admin endpoint
# --------------------------------------------------------------------------


def test_the_admin_endpoint_requires_an_admin(client):
    assert client.post("/admin/payments/reconcile").status_code == 401

    register(client, "nobody@example.com")
    headers = login(client, "nobody@example.com")
    assert client.post("/admin/payments/reconcile", headers=headers).status_code == 403

    admin = make_admin(client)
    assert client.post("/admin/payments/reconcile", headers=admin).status_code == 200


def test_the_admin_endpoint_defaults_to_a_dry_run(client):
    job, customer, worker = accepted_job(client)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    key = f"capture-call:{payment.id}"
    reopen(key)
    admin = make_admin(client)

    body = client.post("/admin/payments/reconcile", headers=admin).json()

    assert body["dry_run"] is True
    assert body["scanned"] == 1
    assert body["succeeded"] == 1
    assert body["outcomes"][0]["provider_ref"] == payment.provider_charge_ref
    assert body["outcomes"][0]["written"] is False
    assert journal_row(key).status is ProviderCallStatus.PENDING

    applied = client.post(
        "/admin/payments/reconcile", json={"dry_run": False}, headers=admin
    ).json()
    assert applied["dry_run"] is False
    assert applied["outcomes"][0]["written"] is True
    assert journal_row(key).status is ProviderCallStatus.SUCCEEDED


def test_the_admin_endpoint_reports_discrepancies_without_repairing_them(client):
    job, customer, worker = accepted_job(client, onboard_worker=True)
    complete_job(client, job, worker)
    payment = payment_for(job["id"])
    with SessionLocal() as db:
        row = db.get(Payment, payment.id)
        row.status = PaymentStatus.CAPTURED
        row.provider_payout_ref = None
        db.execute(delete(LedgerEntry).where(LedgerEntry.txn_key == f"payout:{payment.id}"))
        db.commit()
    reopen(f"payout:{payment.id}#0")
    admin = make_admin(client)

    body = client.post(
        "/admin/payments/reconcile", json={"dry_run": False}, headers=admin
    ).json()

    assert body["discrepancy_count"] == 1
    assert "WORKER PAID, NOT RECORDED" in body["outcomes"][0]["discrepancy"]
    assert payment_for(job["id"]).status is PaymentStatus.CAPTURED, "reported, not repaired"
