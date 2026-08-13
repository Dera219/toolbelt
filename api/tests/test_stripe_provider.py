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

import pytest

from app.modules.payments.provider import ProviderError
from app.modules.payments.stripe_provider import StripePaymentProvider

# conftest blanks TOOLBELT_STRIPE_SECRET_KEY so the domain suite can never
# reach live Stripe; it stashes the real value here for this module alone.
from tests.conftest import STRIPE_TEST_KEY as SECRET_KEY  # noqa: E402

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
        100, "usd", customer_ref, pm_ref, {"job_id": "itest-authorize"}
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

    provider.release(auth_ref)


def test_capture_charges_the_held_amount(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(100, "usd", customer_ref, pm_ref, {"job_id": "itest-capture"})
    charge_ref = provider.capture(auth_ref)

    assert charge_ref.startswith("ch_"), f"expected a charge id, got {charge_ref!r}"
    intent = provider._client.payment_intents.retrieve(auth_ref)
    assert intent.status == "succeeded"
    assert intent.amount_received == 100


def test_release_cancels_the_hold(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(100, "usd", customer_ref, pm_ref, {"job_id": "itest-release"})
    provider.release(auth_ref)

    intent = provider._client.payment_intents.retrieve(auth_ref)
    assert intent.status == "canceled"
    assert intent.amount_received == 0


def test_full_refund_after_capture(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(100, "usd", customer_ref, pm_ref, {"job_id": "itest-refund"})
    charge_ref = provider.capture(auth_ref)

    refund_ref = provider.refund(charge_ref, 100)
    assert refund_ref.startswith("re_")

    charge = provider._client.charges.retrieve(charge_ref)
    assert charge.amount_refunded == 100
    assert charge.refunded is True


def test_partial_refund_leaves_remainder_captured(provider, customer):
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(200, "usd", customer_ref, pm_ref, {"job_id": "itest-partial"})
    charge_ref = provider.capture(auth_ref)

    provider.refund(charge_ref, 75)

    charge = provider._client.charges.retrieve(charge_ref)
    assert charge.amount_refunded == 75
    assert charge.refunded is False, "a partial refund must not mark the charge fully refunded"


def test_end_to_end_dollar_transaction(provider, customer):
    """The Phase 2 exit criterion, as a single test: a real $1.00 authorize →
    capture → refund against Stripe test mode."""
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(100, "usd", customer_ref, pm_ref, {"job_id": "itest-e2e"})
    assert provider._client.payment_intents.retrieve(auth_ref).status == "requires_capture"

    charge_ref = provider.capture(auth_ref)
    assert provider._client.payment_intents.retrieve(auth_ref).amount_received == 100

    provider.refund(charge_ref, 100)
    assert provider._client.charges.retrieve(charge_ref).amount_refunded == 100


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
            provider.authorize(100, "usd", ref, pm_ref, {"job_id": "itest-chargefail"})
    finally:
        try:
            provider._client.customers.delete(ref)
        except Exception:
            pass


def test_capture_unknown_intent_raises_provider_error(provider):
    with pytest.raises(ProviderError):
        provider.capture("pi_nonexistent_intent_id")


def test_refund_unknown_charge_raises_provider_error(provider):
    with pytest.raises(ProviderError):
        provider.refund("ch_nonexistent_charge_id", 100)


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
    ref = provider.transfer(CONNECTED_ACCOUNT, 85, "usd", {"payment_id": "itest-transfer"})
    assert ref.startswith("tr_")

    transfer = provider._client.transfers.retrieve(ref)
    assert transfer.amount == 85
    assert transfer.destination == CONNECTED_ACCOUNT


def test_capture_tolerates_an_already_captured_intent(provider, customer):
    """Stripe can capture and then fail the response. A retry must report the
    capture that already happened, not error forever while our records say the
    money is still only authorized."""
    customer_ref, pm_ref = customer
    auth_ref = provider.authorize(
        1500, "usd", customer_ref, pm_ref, {"job_id": "capture-idempotency"},
    )
    first = provider.capture(auth_ref)
    second = provider.capture(auth_ref)  # would raise "already captured" unguarded
    assert first == second
