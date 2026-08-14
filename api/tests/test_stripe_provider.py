"""Integration tests for StripePaymentProvider against Stripe TEST MODE.

These are the tests that close the Phase 2 exit criterion ("real $1 test
transaction end to end"). Everything else in the suite exercises the domain
through FakePaymentProvider; nothing else touches Stripe's API at all.

Run them:

    export TOOLBELT_STRIPE_SECRET_KEY=sk_test_...
    .venv/bin/python -m pytest tests/test_stripe_provider.py -v

Without that variable the whole module skips, so the default `pytest` run stays
offline, fast, and free.

The transfer test additionally needs a connected account that has completed
Express onboarding, because Stripe will not accept transfers to an account
without an active transfers capability. Supply one with:

    export TOOLBELT_STRIPE_TEST_ACCOUNT=acct_...
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.modules.payments.provider import ProviderError
from app.modules.payments.stripe_provider import StripePaymentProvider

# conftest blanks TOOLBELT_STRIPE_SECRET_KEY so the domain suite can never
# reach live Stripe; it stashes the real value here for this module alone.
from tests.conftest import STRIPE_TEST_KEY as SECRET_KEY  # noqa: E402

# The reconciliation tests at the bottom build their journal rows with the same
# rig the offline suite uses, so the two suites cannot disagree about what a
# `pending` row looks like.
from tests import reconcile_helpers  # noqa: E402

CONNECTED_ACCOUNT = os.environ.get("TOOLBELT_STRIPE_TEST_ACCOUNT", "")

pytestmark = [
    pytest.mark.stripe,
    pytest.mark.skipif(
        not SECRET_KEY,
        reason="TOOLBELT_STRIPE_SECRET_KEY not set — Stripe integration tests skipped",
    ),
]

# Stripe's documented test PaymentMethods.
PM_VISA = "pm_card_visa"
PM_DECLINED = "pm_card_chargeDeclined"


def _key(label: str) -> str:
    """A single-use idempotency key.

    Stripe keeps a key's result for 24 hours, so a fixed string would make the
    second run of a test replay the first run's object instead of exercising the
    call. Tests that are *about* replay build their key once and reuse it
    deliberately.
    """
    return f"itest-{label}-{uuid.uuid4()}"


def _make_payment_method(provider: StripePaymentProvider, token: str = "tok_visa") -> str:
    """Create a concrete PaymentMethod and return its id.

    Necessary because the shared test tokens (`pm_card_visa`) are not real
    PaymentMethod ids — every reference to one mints a *new* PaymentMethod. So
    `attach(pm_card_visa)` and a later `update(default=pm_card_visa)` resolve to
    two different objects, and the second fails as unattached.

    Creating one up front mirrors production exactly: the mobile SDK tokenizes
    the card, hands the API a concrete `pm_…`, and that id is stable thereafter.
    """
    pm = provider._client.payment_methods.create(
        params={"type": "card", "card": {"token": token}}
    )
    return pm.id


def _guard_test_mode() -> None:
    """Refuse to run against a live key.

    This is the single most important line in the file. These tests create
    customers, authorize cards, capture funds, and issue refunds. Against a live
    key that is real money and real customer records.
    """
    if SECRET_KEY.startswith("sk_live_") or SECRET_KEY.startswith("rk_live_"):
        pytest.fail(
            "REFUSING TO RUN: TOOLBELT_STRIPE_SECRET_KEY is a LIVE key. "
            "These tests move money. Use an sk_test_ key.",
            pytrace=False,
        )
    if not SECRET_KEY.startswith(("sk_test_", "rk_test_")):
        pytest.fail(
            f"Unrecognized key prefix {SECRET_KEY[:8]!r}; expected sk_test_ or rk_test_.",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def provider() -> StripePaymentProvider:
    _guard_test_mode()
    return StripePaymentProvider(SECRET_KEY)


@pytest.fixture()
def customer(provider: StripePaymentProvider):
    """A Stripe customer with an attached, chargeable card.

    Yields (customer_ref, payment_method_ref).

    The second value is load-bearing. `pm_card_visa` is a *shared* test token:
    attaching it mints a brand-new PaymentMethod with a different id, so
    authorizing against the literal string fails with "The customer does not
    have a payment method with the ID pm_card_visa". Real integrations never
    hit this — the mobile SDK produces a concrete pm_… that keeps its id
    through attach — but the tests have to read back what was actually created.
    """
    ref = provider.create_customer("toolbelt-itest@example.com")
    pm_ref = _make_payment_method(provider)
    provider.attach_payment_method(ref, pm_ref)
    yield ref, pm_ref
    try:
        provider._client.customers.delete(ref)
    except Exception:
        # Cleanup is best-effort; a leftover test-mode customer is harmless.
        pass


# --------------------------------------------------------------------------
# Customer and payment method
# --------------------------------------------------------------------------

def test_create_customer_returns_stripe_customer_id(provider):
    ref = provider.create_customer("toolbelt-itest-create@example.com")
    assert ref.startswith("cus_")
    try:
        provider._client.customers.delete(ref)
    except Exception:
        pass


def test_attach_payment_method_sets_default(provider, customer):
    customer_ref, _ = customer
    fetched = provider._client.customers.retrieve(customer_ref)
    assert fetched.invoice_settings.default_payment_method is not None, (
        "attach_payment_method must set the default payment method, otherwise "
        "off-session authorization has nothing to charge"
    )


def test_attach_invalid_payment_method_raises_provider_error(provider, customer):
    customer_ref, _ = customer
    with pytest.raises(ProviderError):
        provider.attach_payment_method(customer_ref, "pm_does_not_exist")


# --------------------------------------------------------------------------
# The money loop
# --------------------------------------------------------------------------

def test_authorize_holds_without_charging(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-authorize"},
        idempotency_key=_key("authorize"),
    )
    assert auth_ref.startswith("pi_")

    intent = provider._client.payment_intents.retrieve(auth_ref)
    assert intent.status == "requires_capture", (
        "authorize() must leave the intent uncaptured — the customer is not "
        "charged until the job is confirmed complete"
    )
    assert intent.amount == 100
    assert intent.amount_received == 0
    assert intent.metadata["job_id"] == "itest-authorize"

    provider.release(auth_ref, idempotency_key=_key("release"))


def test_capture_charges_the_held_amount(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-capture"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    assert charge_ref.startswith("ch_"), f"expected a charge id, got {charge_ref!r}"
    intent = provider._client.payment_intents.retrieve(auth_ref)
    assert intent.status == "succeeded"
    assert intent.amount_received == 100


def test_release_cancels_the_hold(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-release"},
        idempotency_key=_key("authorize"),
    )
    provider.release(auth_ref, idempotency_key=_key("release"))

    intent = provider._client.payment_intents.retrieve(auth_ref)
    assert intent.status == "canceled"
    assert intent.amount_received == 0


def test_full_refund_after_capture(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-refund"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    refund_ref = provider.refund(charge_ref, 100, idempotency_key=_key("refund"))
    assert refund_ref.startswith("re_")

    charge = provider._client.charges.retrieve(charge_ref)
    assert charge.amount_refunded == 100
    assert charge.refunded is True


def test_partial_refund_leaves_remainder_captured(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        200, "usd", customer_ref, pm_ref, {"job_id": "itest-partial"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    provider.refund(charge_ref, 75, idempotency_key=_key("refund"))

    charge = provider._client.charges.retrieve(charge_ref)
    assert charge.amount_refunded == 75
    assert charge.refunded is False, "a partial refund must not mark the charge fully refunded"


def test_end_to_end_dollar_transaction(provider, customer):
    """The Phase 2 exit criterion, as a single test: a real $1.00 authorize →
    capture → refund against Stripe test mode."""
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-e2e"},
        idempotency_key=_key("authorize"),
    )
    assert provider._client.payment_intents.retrieve(auth_ref).status == "requires_capture"

    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))
    assert provider._client.payment_intents.retrieve(auth_ref).amount_received == 100

    provider.refund(charge_ref, 100, idempotency_key=_key("refund"))
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 100


# --------------------------------------------------------------------------
# Refund idempotency — the proof against Stripe itself
# --------------------------------------------------------------------------

def test_refund_replay_returns_the_original_and_does_not_refund_twice(provider, customer):
    """The defect, closed at the rail that actually holds the money.

    refund() ran with no idempotency key, and it runs before the refund is
    recorded locally: any failure afterwards rolls the database back over a
    refund that already happened, and the admin's retry sent the customer their
    money a second time. The domain suite proves the flow against
    FakePaymentProvider; this proves Stripe's half of it.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        400, "usd", customer_ref, pm_ref, {"job_id": "itest-refund-replay"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    # Built once and reused on purpose — this is the retry after a rollback.
    key = _key("refund-replay")
    first = provider.refund(charge_ref, 250, idempotency_key=key)
    assert first.startswith("re_")
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 250

    replay = provider.refund(charge_ref, 250, idempotency_key=key)
    assert replay == first, "the same key must return the original refund"
    charge = provider._client.charges.retrieve(charge_ref)
    assert charge.amount_refunded == 250, "a replay must not refund the customer twice"
    assert charge.refunded is False, "250 of 400 is still a partial refund"


def test_refund_key_reused_at_a_different_amount_is_refused_loudly(provider, customer):
    """The trade the generation-based key makes, verified rather than assumed.

    The key covers the refund generation, not the amount, so a retry at a
    different amount arrives as the same key with a different body. Stripe
    answers with an idempotency_error — "Keys for idempotent requests can only
    be used with the same parameters they were first used with" — instead of
    issuing a second real refund, and the provider turns that into a message
    naming what an operator has to do. A key that included the amount would
    quietly refund the customer twice.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        400, "usd", customer_ref, pm_ref, {"job_id": "itest-refund-mismatch"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    key = _key("refund-mismatch")
    provider.refund(charge_ref, 250, idempotency_key=key)
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 250

    with pytest.raises(ProviderError) as raised:
        provider.refund(charge_ref, 100, idempotency_key=key)

    message = str(raised.value)
    assert "idempotency key" in message and "reconcile" in message.lower(), message
    assert raised.value.definitive is False, (
        "a key/parameter mismatch must never be classified as 'nothing moved' — "
        "that reads as permission to mint a fresh key and refund again"
    )
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 250, (
        "the refused retry must not have refunded anything"
    )


# --------------------------------------------------------------------------
# Failure paths
# --------------------------------------------------------------------------

def test_declined_card_raises_provider_error_at_attach(provider):
    """A card that fails validation is rejected when it is attached.

    Stripe validates the card at attach time, so a hard-declining card never
    reaches authorize(). What matters is that the failure surfaces as
    ProviderError rather than a raw StripeError: service.py catches
    ProviderError and returns 402, and a leaked stripe.CardError would escape
    that handler and become a 500.
    """
    ref = provider.create_customer("toolbelt-itest-declined@example.com")
    try:
        pm_ref = _make_payment_method(provider, token="tok_chargeDeclined")
        with pytest.raises(ProviderError):
            provider.attach_payment_method(ref, pm_ref)
    finally:
        try:
            provider._client.customers.delete(ref)
        except Exception:
            pass


def test_card_declining_at_charge_raises_provider_error(provider):
    """A card that attaches cleanly but declines when charged.

    This is the path a real customer hits when their card is fine on file but
    fails at booking time — insufficient funds, a hold, a fraud block.
    """
    ref = provider.create_customer("toolbelt-itest-chargefail@example.com")
    try:
        pm_ref = _make_payment_method(provider, token="tok_chargeCustomerFail")
        provider.attach_payment_method(ref, pm_ref)
        with pytest.raises(ProviderError):
            provider.authorize(
                100, "usd", ref, pm_ref, {"job_id": "itest-chargefail"},
                idempotency_key=_key("authorize"),
            )
    finally:
        try:
            provider._client.customers.delete(ref)
        except Exception:
            pass


def test_capture_unknown_intent_raises_provider_error(provider):
    with pytest.raises(ProviderError):
        provider.capture("pi_nonexistent_intent_id", idempotency_key=_key("capture"))


def test_refund_unknown_charge_raises_provider_error(provider):
    with pytest.raises(ProviderError):
        provider.refund("ch_nonexistent_charge_id", 100, idempotency_key=_key("refund"))


# --------------------------------------------------------------------------
# Connect payouts
# --------------------------------------------------------------------------

def test_create_payout_account_returns_account_and_onboarding_url(provider):
    """Worker onboarding via Accounts v2.

    Regression guard: this previously called Accounts v1, which Stripe rejects
    for new Connect integrations, so no worker could be onboarded at all.
    """
    account_ref, onboarding_url = provider.create_payout_account(
        "toolbelt-itest-worker@example.com"
    )
    assert account_ref.startswith("acct_")
    assert onboarding_url.startswith("https://connect.stripe.com/")

    account = provider._client.v2.core.accounts.retrieve(
        account_ref, params={"include": ["configuration.recipient"]}
    )
    assert "recipient" in (account.applied_configurations or []), (
        "the account must have the recipient configuration applied, or it "
        "cannot receive transfers"
    )


@pytest.mark.skipif(
    not CONNECTED_ACCOUNT,
    reason=(
        "TOOLBELT_STRIPE_TEST_ACCOUNT not set — needs a connected account that has "
        "completed Express onboarding, which cannot be automated from a test"
    ),
)
def test_transfer_to_connected_account(provider):
    ref = provider.transfer(
        CONNECTED_ACCOUNT, 85, "usd", {"payment_id": "itest-transfer"},
        idempotency_key=f"itest-transfer-{uuid.uuid4()}",
    )
    assert ref.startswith("tr_")

    transfer = provider._client.transfers.retrieve(ref)
    assert transfer.amount == 85
    assert transfer.destination == CONNECTED_ACCOUNT


@pytest.mark.skipif(
    not CONNECTED_ACCOUNT,
    reason=(
        "TOOLBELT_STRIPE_TEST_ACCOUNT not set — needs a connected account that has "
        "completed Express onboarding, which cannot be automated from a test"
    ),
)
def test_reverse_transfer_claws_back_and_replays_idempotently(provider):
    """Refunds after payout claw the worker share back via a transfer reversal.

    The domain suite proves the flow against FakePaymentProvider; this is the
    one test that proves Stripe's side: a partial reversal lands with the right
    amount, and replaying the same idempotency key returns the original
    reversal instead of collecting from the worker twice.
    """
    transfer_ref = provider.transfer(
        CONNECTED_ACCOUNT, 85, "usd", {"payment_id": "itest-reversal"},
        idempotency_key=f"itest-reversal-setup-{uuid.uuid4()}",
    )
    key = f"itest-reverse-{uuid.uuid4()}"

    reversal_ref = provider.reverse_transfer(transfer_ref, 40, idempotency_key=key)
    assert reversal_ref.startswith("trr_")

    transfer = provider._client.transfers.retrieve(transfer_ref)
    assert transfer.amount_reversed == 40
    assert transfer.reversed is False  # partial, not full

    replay_ref = provider.reverse_transfer(transfer_ref, 40, idempotency_key=key)
    assert replay_ref == reversal_ref, "same key must return the original reversal"
    transfer = provider._client.transfers.retrieve(transfer_ref)
    assert transfer.amount_reversed == 40, "replay must not reverse a second time"


# --------------------------------------------------------------------------
# Reconciliation against the real API
# --------------------------------------------------------------------------
#
# The offline suite proves every branch of the sweeper against
# FakePaymentProvider. These prove the half that matters most and that a fake
# cannot: that a `pending` journal row resolves correctly when the thing being
# asked is Stripe itself. Both directions are here on purpose — a reconciler
# that can only recognise success is a reconciler that cannot tell "nothing
# happened" from "I cannot tell", which is its entire job.


def _local_payment(client, real_auth_ref: str, real_charge_ref: str | None):
    """A real Payment row wired to real Stripe objects.

    The rows go through the normal flow (so foreign keys, the job, and both
    users are genuine) and then have their provider references swapped for the
    ones created above against test-mode Stripe. That is exactly the state the
    sweeper meets in production: local bookkeeping on one side, Stripe on the
    other, and a journal row that never learned how the call ended.
    """
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.modules.payments.models import Payment

    job, _customer, worker = reconcile_helpers.accepted_job(client)
    with SessionLocal() as db:
        payment = db.scalar(select(Payment).where(Payment.job_id == job["id"]))
        payment.provider_auth_ref = real_auth_ref
        payment.provider_charge_ref = real_charge_ref
        db.commit()
        db.refresh(payment)
        return payment


@pytest.fixture()
def stripe_reconcile(provider, monkeypatch):
    """Point the sweeper at real Stripe.

    conftest blanks the secret key so the domain suite can never reach live
    Stripe, which means get_payment_provider() returns the fake. Overriding it
    here is what makes these tests exercise the real read path.
    """
    from app.modules.payments import reconcile

    monkeypatch.setattr(reconcile, "get_payment_provider", lambda: provider)
    return reconcile


def test_reconciling_a_pending_capture_against_stripe_finds_the_real_charge(
    client, provider, customer, stripe_reconcile
):
    """The crash between the call and the completion write, resolved by Stripe.

    A real $1.00 authorize + capture happens, the local journal row is left the
    way a crash leaves it, and the sweep has to come back with the charge id
    Stripe actually created — not a guess, and not a replay.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-reconcile-capture"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))

    payment = _local_payment(client, auth_ref, None)
    key = f"capture-call:{payment.id}"
    reconcile_helpers.pending(key, "capture", payment.id, {"auth_ref": auth_ref})

    outcome = reconcile_helpers.only(reconcile_helpers.sweep())

    assert outcome.resolution is stripe_reconcile.Resolution.SUCCEEDED
    assert outcome.provider_ref == charge_ref, (
        "the sweeper must record the charge Stripe actually created, because the "
        "journal replays that reference to the refund path without asking again"
    )
    assert reconcile_helpers.journal_row(key).provider_ref == charge_ref
    # The local payment still says AUTHORIZED with no charge reference, so this
    # is a genuine books-vs-Stripe disagreement and must be escalated, not fixed.
    assert outcome.discrepancy is not None
    assert "MONEY TAKEN, NOT RECORDED" in outcome.discrepancy


def test_reconciling_a_capture_that_never_happened_against_stripe_says_so(
    client, provider, customer, stripe_reconcile
):
    """The other verdict, and the one that is easy to get wrong.

    The authorization is a real, live, uncaptured hold at Stripe. That is
    positive evidence the capture never landed — the answer must be `failed`,
    not `unknown` and certainly not `succeeded`.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-reconcile-nocapture"},
        idempotency_key=_key("authorize"),
    )
    try:
        payment = _local_payment(client, auth_ref, None)
        key = f"capture-call:{payment.id}"
        reconcile_helpers.pending(key, "capture", payment.id, {"auth_ref": auth_ref})

        outcome = reconcile_helpers.only(reconcile_helpers.sweep())

        assert outcome.resolution is stripe_reconcile.Resolution.FAILED
        assert outcome.provider_ref is None
        assert "uncaptured hold" in outcome.detail
        assert provider._client.payment_intents.retrieve(auth_ref).amount_received == 0, (
            "the sweeper must not have captured anything while looking"
        )
    finally:
        provider.release(auth_ref, idempotency_key=_key("release"))


def test_reconciling_a_pending_refund_against_stripe_finds_the_real_refund(
    client, provider, customer, stripe_reconcile
):
    """Identification by fingerprint, proved against Stripe's own refund list.

    Nothing local records which refund object this call produced — that write is
    the one that was lost. The refund is picked out of the charge's refunds by
    rebuilding the call's parameters from each candidate and matching the hash
    the journal wrote before dialling out.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        400, "usd", customer_ref, pm_ref, {"job_id": "itest-reconcile-refund"},
        idempotency_key=_key("authorize"),
    )
    charge_ref = provider.capture(auth_ref, idempotency_key=_key("capture"))
    refund_ref = provider.refund(charge_ref, 250, idempotency_key=_key("refund"))

    payment = _local_payment(client, auth_ref, charge_ref)
    key = f"refund-call:{payment.id}:0"
    reconcile_helpers.pending(
        key, "refund", payment.id, {"charge": charge_ref, "amount": 250}
    )

    outcome = reconcile_helpers.only(reconcile_helpers.sweep())

    assert outcome.resolution is stripe_reconcile.Resolution.SUCCEEDED
    assert outcome.provider_ref == refund_ref
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 250, (
        "reconciliation is a read: the customer must not have been refunded twice"
    )


@pytest.mark.skipif(
    not CONNECTED_ACCOUNT,
    reason=(
        "TOOLBELT_STRIPE_TEST_ACCOUNT not set — needs a connected account that has "
        "completed Express onboarding, which cannot be automated from a test"
    ),
)
def test_reconciling_a_pending_payout_against_stripe_finds_the_real_transfer(
    client, provider, stripe_reconcile
):
    """A payout is identified by metadata, so this is where reading Stripe's
    objects the wrong way stays invisible offline.

    A fake hands back a plain dict for metadata; Stripe hands back a StripeObject
    with no `.get`. Only a test that reads a real transfer catches that, and it
    is the difference between "the worker was paid" and an exception at 3am.
    """
    import time

    from sqlalchemy import select, update

    from app.core.db import SessionLocal
    from app.modules.payments.models import (
        LedgerEntry,
        Payment,
        PaymentStatus,
        PayoutAccount,
        ProviderCall,
    )

    # 100 cents at the 15% take-rate leaves the worker 85 — the amount the other
    # Connect tests move, and small enough to be free in test mode.
    job, _customer, worker = reconcile_helpers.accepted_job(client, price_cents=100)
    reconcile_helpers.complete_job(client, job, worker)
    payment = reconcile_helpers.payment_for(job["id"])
    assert payment.status is PaymentStatus.CAPTURED, "the worker is not onboarded yet"
    worker_id, worker_net, currency = (
        payment.worker_id, payment.worker_net_cents, payment.currency
    )

    # The payment is renumbered to something unique before its id is stamped into
    # Stripe metadata. The suite starts from an empty database every run, so
    # every run would otherwise claim payment id 1 — and the test-mode connected
    # account is shared and keeps every transfer forever. The second run would
    # then find two transfers carrying payment_id=1 and correctly report a DOUBLE
    # PAYOUT that only exists because the test collided with its own history.
    payment_id = int(time.time()) % 2_000_000_000
    with SessionLocal() as db:
        db.execute(
            update(LedgerEntry).where(LedgerEntry.payment_id == payment.id)
            .values(payment_id=payment_id)
        )
        db.execute(
            update(ProviderCall).where(ProviderCall.payment_id == payment.id)
            .values(payment_id=payment_id)
        )
        db.execute(update(Payment).where(Payment.id == payment.id).values(id=payment_id))
        db.add(
            PayoutAccount(
                user_id=worker_id,
                provider_account_ref=CONNECTED_ACCOUNT,
                payouts_enabled=True,
            )
        )
        db.commit()

    transfer_ref = provider.transfer(
        CONNECTED_ACCOUNT, worker_net, "usd",
        {"payment_id": str(payment_id)},
        idempotency_key=_key("reconcile-payout"),
    )
    key = f"payout:{payment_id}#0"
    reconcile_helpers.pending(
        key,
        "transfer",
        payment_id,
        {
            "destination": CONNECTED_ACCOUNT,
            "amount": worker_net,
            "currency": currency,
        },
    )

    outcome = reconcile_helpers.only(reconcile_helpers.sweep())

    assert outcome.resolution is stripe_reconcile.Resolution.SUCCEEDED
    assert outcome.provider_ref == transfer_ref
    # The payment still reads CAPTURED with no payout reference, so the worker
    # has money we have no record of sending. Escalated, never repaired.
    assert "WORKER PAID, NOT RECORDED" in (outcome.discrepancy or "")
    with SessionLocal() as db:
        after = db.scalar(select(Payment).where(Payment.id == payment_id))
        assert after.status is PaymentStatus.CAPTURED
        assert after.provider_payout_ref is None


def test_capture_tolerates_an_already_captured_intent(provider, customer):
    """Stripe can capture and then fail the response. A retry must report the
    capture that already happened, not error forever while our records say the
    money is still only authorized.

    Both retry paths matter, because the capture key is stable per payment:
    within 24 hours the retry replays through the idempotency key, and after
    Stripe prunes that key the same retry arrives as a fresh request and lands
    on "already captured" — which is what the retrieve fallback is for.
    """
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        1500, "usd", customer_ref, pm_ref, {"job_id": "capture-idempotency"},
        idempotency_key=_key("authorize"),
    )
    key = _key("capture-idempotency")
    first = provider.capture(auth_ref, idempotency_key=key)
    replayed = provider.capture(auth_ref, idempotency_key=key)
    assert replayed == first, "the same key must replay the original capture"

    # A different key is the post-pruning retry: a real second call, which
    # Stripe rejects and the retrieve fallback rescues.
    after_pruning = provider.capture(auth_ref, idempotency_key=_key("capture-again"))
    assert after_pruning == first
