"""Payment provider seam. All money-rail operations go through this interface;
the domain logic (service.py, ledger.py) never imports a provider SDK directly.

Dev/test: FakePaymentProvider (in-process, inspectable).
Prod: StripePaymentProvider (stripe_provider.py), selected when a key is configured.

The interface has two halves. Everything that moves money takes an
`idempotency_key` and is called only through journal.execute_provider_call. The
`lookup_*` methods at the bottom move nothing: they exist so the reconciliation
sweeper (reconcile.py) can ask what actually happened without re-issuing a
request, and they return plain dataclasses so the sweeper never touches a
provider SDK type.
"""

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.core.config import get_settings


def _now() -> datetime:
    """Timestamps the fake provider stamps on the objects it records.

    Timezone-aware on purpose: the sweeper compares these against journal
    `created_at` values, and mixing aware and naive datetimes raises rather than
    comparing wrong — which is the failure mode worth having.
    """
    return datetime.now(timezone.utc)


class ProviderError(Exception):
    """A money operation was declined or failed at the provider.

    `definitive` means the provider rejected the request outright and nothing
    moved — an insufficient balance, a bad argument. Those are safe to retry
    under a new idempotency key. When it is False the outcome is unknown (a
    timeout, a dropped connection) and the retry must reuse the original key so
    the provider deduplicates it rather than paying twice.
    """

    def __init__(self, message: str, *, definitive: bool = False) -> None:
        super().__init__(message)
        self.definitive = definitive


# ---------------------------------------------------------------- read-only reconciliation
#
# What the sweeper is allowed to know, expressed in this codebase's vocabulary
# rather than Stripe's. Two reasons the translation happens here and not in
# reconcile.py: the domain must not import a provider SDK (the rule the whole
# module exists to hold), and FakePaymentProvider has to be able to answer the
# same questions so every branch of the sweeper is reachable offline.


class AuthorizationState(str, enum.Enum):
    """What a customer-side authorization is currently doing at the provider.

    Deliberately smaller than Stripe's PaymentIntent status set, because the
    sweeper only ever needs to answer three questions: is money held, was it
    taken, was the hold let go. Everything that is none of those is IN_FLIGHT —
    the provider has not finished deciding, so neither can we.
    """

    HELD = "held"  # authorized, uncaptured: the hold is in place
    CAPTURED = "captured"  # funds taken from the customer
    CANCELLED = "cancelled"  # the hold was released
    NO_HOLD = "no_hold"  # the object exists but never reached a hold (declined)
    IN_FLIGHT = "in_flight"  # still resolving at the provider; unsafe to judge


@dataclass(frozen=True)
class AuthorizationRecord:
    """A customer-side authorization as the provider currently sees it."""

    ref: str
    state: AuthorizationState
    amount_cents: int
    currency: str
    customer_ref: str | None
    payment_method_ref: str | None
    # The charge produced by a capture. This is what `capture()` returns, so it
    # is the reference a reconciled capture must be recorded under.
    charge_ref: str | None
    # metadata["job_id"] as the provider stored it — a string, always.
    job_id: str | None


@dataclass(frozen=True)
class RefundRecord:
    ref: str
    charge_ref: str
    amount_cents: int
    # False for a refund the provider later failed or cancelled: the money came
    # back to us, so it must not be counted as a refund that landed.
    settled: bool


@dataclass(frozen=True)
class TransferRecord:
    ref: str
    account_ref: str
    amount_cents: int
    currency: str
    # metadata["payment_id"] as the provider stored it — a string, always.
    payment_id: str | None


@dataclass(frozen=True)
class ReversalRecord:
    ref: str
    transfer_ref: str
    amount_cents: int


class PaymentProvider(Protocol):
    def create_customer(self, email: str) -> str: ...
    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> str:
        """Attach a payment method and return its canonical id. Providers accept
        shorthand test tokens that resolve to a *new* object on each use, so the
        returned id — not the submitted one — is what must be stored."""
        ...
    def create_payout_account(self, email: str) -> tuple[str, str]:
        """Returns (account_ref, onboarding_url)."""
        ...

    def payouts_enabled(self, account_ref: str) -> bool:
        """Whether the connected account has finished onboarding and can receive
        transfers. Polled on read so local development does not depend on
        webhook delivery reaching a laptop."""
        ...

    def onboarding_link(self, account_ref: str) -> str:
        """A fresh hosted-onboarding URL for an existing account. Provider links
        expire and are single-use, so resuming always mints a new one."""
        ...

    def create_card_setup(self, customer_ref: str) -> dict:
        """Everything a native payment sheet needs to collect and save a card:
        a setup-intent client secret, a short-lived customer key, and the
        customer id. No card data ever reaches our server."""
        ...

    def create_card_setup_session(self, customer_ref: str, return_url: str) -> str:
        """Hosted card-entry page URL — the browser equivalent of the sheet."""
        ...

    def latest_payment_method(self, customer_ref: str) -> str | None:
        """Most recently attached payment method, of any type. Used only as a
        fallback — the setup reference below is exact."""
        ...

    def payment_method_from_setup(self, setup_ref: str) -> str | None:
        """Resolve what a completed setup actually saved. Accepts either a
        setup-intent or a hosted-session reference."""
        ...

    def set_default_payment_method(self, customer_ref: str, payment_method_ref: str) -> None: ...

    # Every method below moves money, and every one of them takes an
    # `idempotency_key`. It is required rather than optional on purpose: the one
    # call that lacked it — refund — is the one that could refund a customer
    # twice, and an optional parameter is an invitation to forget it again. The
    # keys are built in service.py and the calls are made through
    # journal.execute_provider_call, never directly.
    def authorize(
        self, amount_cents: int, currency: str, customer_ref: str, payment_method_ref: str,
        metadata: dict, idempotency_key: str,
    ) -> str: ...
    def capture(self, auth_ref: str, idempotency_key: str) -> str: ...
    def release(self, auth_ref: str, idempotency_key: str) -> None: ...
    def refund(self, charge_ref: str, amount_cents: int, idempotency_key: str) -> str:
        """Return part or all of a captured charge to the customer.

        The key is keyed on the refund *generation* — the amount already
        refunded before this one — and deliberately not on the new amount. See
        `refund_idempotency_key` in service.py for why that trade is the right
        way round.
        """
        ...

    def transfer(
        self, account_ref: str, amount_cents: int, currency: str, metadata: dict,
        idempotency_key: str,
    ) -> str:
        """Send funds to a connected account.

        `idempotency_key` must be stable per logical payout. A transfer executes
        at the provider before any local row is written, so a failure later in
        the same transaction rolls back the database while the money has already
        moved. Replaying the same key must return the original transfer rather
        than sending a second one.
        """
        ...

    def reverse_transfer(
        self, transfer_ref: str, amount_cents: int, idempotency_key: str,
    ) -> str:
        """Claw back part or all of a transfer from a connected account.

        Used when a refund lands on a payment whose worker share has already
        been paid out. Same idempotency contract as `transfer`: the reversal
        executes at the provider before any local row is written, so replaying
        the same key must return the original reversal, never collect twice.
        """
        ...

    # Every method below is a READ. None of them takes an idempotency key,
    # because none of them can create anything. They are what lets the sweeper
    # resolve a `pending` journal row without replaying the call that produced
    # it — a replay is a mutation whose outcome depends on whether the original
    # request ever reached the provider, which is precisely the thing we do not
    # know. See reconcile.py.
    def lookup_authorization(self, auth_ref: str) -> AuthorizationRecord | None:
        """The current state of one authorization, or None if it does not exist."""
        ...

    def lookup_authorizations_for_customer(
        self, customer_ref: str, *, since: datetime
    ) -> list[AuthorizationRecord]:
        """Every authorization created for this customer since `since`.

        The only way to find an authorization whose reference was never stored
        locally: the crash that leaves an `authorize` pending happens before the
        Payment row exists, so there is nothing to retrieve by id. Bounded by
        time because the answer is scanned, not indexed.
        """
        ...

    def lookup_refunds(self, charge_ref: str) -> list[RefundRecord]:
        """Every refund the provider holds against this charge."""
        ...

    def lookup_transfers_to(
        self, account_ref: str, *, since: datetime
    ) -> list[TransferRecord]:
        """Every transfer sent to this connected account since `since`."""
        ...

    def lookup_transfer_reversals(self, transfer_ref: str) -> list[ReversalRecord]:
        """Every reversal recorded against this transfer."""
        ...


class FakePaymentProvider:
    """In-process provider for dev and tests. Records every call; supports failure
    injection so decline paths are testable.

    It also answers the `lookup_*` reads, which is what lets the offline suite
    exercise every branch of the reconciliation sweeper. That matters more than
    it looks: the sweeper's whole job is deciding between "nothing happened" and
    "I cannot tell", and a decision procedure that is only ever exercised
    against the real API is a decision procedure nobody can test the edges of.
    Its recorded objects therefore carry the same fields Stripe's do — amounts,
    currency, metadata, and the state an authorization moves through — rather
    than the minimum each call needed to return.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._seq = 0
        self.fail_next_authorize = False
        self.customers: list[str] = []
        self.attached: list[tuple[str, str]] = []
        self.accounts: list[str] = []
        self.enabled_accounts: set[str] = set()
        self.setups: list[tuple[str, str]] = []
        self.setup_sessions: list[tuple[str, str]] = []
        self.setup_payment_methods: dict[str, str] = {}
        self.authorizations: list[dict] = []
        self.captures: list[str] = []
        self.releases: list[str] = []
        self.refunds: list[tuple[str, int]] = []
        self._refund_keys: dict[str, str] = {}
        self.transfers: list[dict] = []
        self._transfer_keys: dict[str, str] = {}
        self.fail_next_transfer = False
        self.transfer_reversals: list[dict] = []
        self._reversal_keys: dict[str, str] = {}

    def _ref(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_fake_{self._seq}"

    def create_customer(self, email: str) -> str:
        ref = self._ref("cus")
        self.customers.append(ref)
        return ref

    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> str:
        self.attached.append((customer_ref, payment_method_ref))
        return payment_method_ref

    def create_payout_account(self, email: str) -> tuple[str, str]:
        ref = self._ref("acct")
        self.accounts.append(ref)
        return ref, f"https://onboarding.fake/{ref}"

    def payouts_enabled(self, account_ref: str) -> bool:
        # The fake provider only flips via the webhook path, which the tests
        # drive explicitly; polling must not silently enable payouts.
        return account_ref in self.enabled_accounts

    def onboarding_link(self, account_ref: str) -> str:
        return f"https://onboarding.fake/{account_ref}"

    def create_card_setup(self, customer_ref: str) -> dict:
        ref = self._ref("seti")
        self.setups.append((customer_ref, ref))
        return {
            "setup_ref": ref,
            "setup_intent_client_secret": f"{ref}_secret_fake",
            "customer_ephemeral_key_secret": f"ek_fake_{self._seq}",
            "customer_ref": customer_ref,
        }

    def create_card_setup_session(self, customer_ref: str, return_url: str) -> str:
        ref = self._ref("cs")
        self.setup_sessions.append((customer_ref, ref))
        return f"https://checkout.fake/{ref}"

    def latest_payment_method(self, customer_ref: str) -> str | None:
        saved = [pm for cus, pm in self.attached if cus == customer_ref]
        return saved[-1] if saved else None

    def payment_method_from_setup(self, setup_ref: str) -> str | None:
        return self.setup_payment_methods.get(setup_ref)

    def set_default_payment_method(self, customer_ref: str, payment_method_ref: str) -> None:
        self.attached.append((customer_ref, payment_method_ref))

    def authorize(
        self, amount_cents: int, currency: str, customer_ref: str, payment_method_ref: str,
        metadata: dict, idempotency_key: str,
    ) -> str:
        if self.fail_next_authorize:
            self.fail_next_authorize = False
            raise ProviderError("Card declined")
        ref = self._ref("auth")
        self.authorizations.append(
            {
                "ref": ref,
                "amount_cents": amount_cents,
                "currency": currency,
                "idempotency_key": idempotency_key,
                # The reconciliation reads need the same handles Stripe exposes:
                # who was charged, with what, what state the hold is in now, and
                # what charge a capture produced.
                "customer_ref": customer_ref,
                "payment_method_ref": payment_method_ref,
                "state": AuthorizationState.HELD,
                "charge_ref": None,
                "created_at": _now(),
                **metadata,
            }
        )
        return ref

    def _authorization(self, auth_ref: str) -> dict | None:
        return next((a for a in self.authorizations if a["ref"] == auth_ref), None)

    def capture(self, auth_ref: str, idempotency_key: str) -> str:
        self.captures.append(auth_ref)
        charge_ref = auth_ref.replace("auth_", "ch_")
        record = self._authorization(auth_ref)
        if record is not None:
            record["state"] = AuthorizationState.CAPTURED
            record["charge_ref"] = charge_ref
        return charge_ref

    def release(self, auth_ref: str, idempotency_key: str) -> None:
        self.releases.append(auth_ref)
        record = self._authorization(auth_ref)
        if record is not None:
            record["state"] = AuthorizationState.CANCELLED

    def refund(self, charge_ref: str, amount_cents: int, idempotency_key: str) -> str:
        # Same replay contract as transfer: a reused key returns the original
        # refund rather than sending the customer their money a second time.
        if idempotency_key in self._refund_keys:
            return self._refund_keys[idempotency_key]
        ref = self._ref("re")
        self._refund_keys[idempotency_key] = ref
        self.refunds.append(
            {
                "ref": ref,
                "charge_ref": charge_ref,
                "amount_cents": amount_cents,
                "settled": True,
                "created_at": _now(),
            }
        )
        return ref

    def transfer(
        self, account_ref: str, amount_cents: int, currency: str, metadata: dict,
        idempotency_key: str,
    ) -> str:
        # Mirror the provider's idempotency so tests can prove a replay does not
        # move money twice.
        if idempotency_key in self._transfer_keys:
            return self._transfer_keys[idempotency_key]
        ref = self._ref("tr")
        self._transfer_keys[idempotency_key] = ref
        self.transfers.append(
            {
                "ref": ref,
                "account_ref": account_ref,
                "amount_cents": amount_cents,
                "currency": currency,
                "created_at": _now(),
                **metadata,
            }
        )
        return ref

    def reverse_transfer(
        self, transfer_ref: str, amount_cents: int, idempotency_key: str,
    ) -> str:
        # Same replay contract as transfer: a reused key returns the original
        # reversal instead of collecting from the worker a second time.
        if idempotency_key in self._reversal_keys:
            return self._reversal_keys[idempotency_key]
        ref = self._ref("trr")
        self._reversal_keys[idempotency_key] = ref
        self.transfer_reversals.append(
            {
                "ref": ref,
                "transfer_ref": transfer_ref,
                "amount_cents": amount_cents,
                "created_at": _now(),
            }
        )
        return ref

    # ------------------------------------------------------------ reconciliation reads

    @staticmethod
    def _as_authorization(record: dict) -> AuthorizationRecord:
        return AuthorizationRecord(
            ref=record["ref"],
            state=record["state"],
            amount_cents=record["amount_cents"],
            currency=record["currency"],
            customer_ref=record.get("customer_ref"),
            payment_method_ref=record.get("payment_method_ref"),
            charge_ref=record.get("charge_ref"),
            # Stored as a string here for the same reason Stripe stores it as
            # one: provider metadata is string-valued, and a sweeper that
            # matched on an int locally would not match in production.
            job_id=record.get("job_id"),
        )

    def lookup_authorization(self, auth_ref: str) -> AuthorizationRecord | None:
        record = self._authorization(auth_ref)
        return None if record is None else self._as_authorization(record)

    def lookup_authorizations_for_customer(
        self, customer_ref: str, *, since: datetime
    ) -> list[AuthorizationRecord]:
        return [
            self._as_authorization(a)
            for a in self.authorizations
            if a.get("customer_ref") == customer_ref and a["created_at"] >= since
        ]

    def lookup_refunds(self, charge_ref: str) -> list[RefundRecord]:
        return [
            RefundRecord(
                ref=r["ref"],
                charge_ref=r["charge_ref"],
                amount_cents=r["amount_cents"],
                settled=r["settled"],
            )
            for r in self.refunds
            if r["charge_ref"] == charge_ref
        ]

    def lookup_transfers_to(
        self, account_ref: str, *, since: datetime
    ) -> list[TransferRecord]:
        return [
            TransferRecord(
                ref=t["ref"],
                account_ref=t["account_ref"],
                amount_cents=t["amount_cents"],
                currency=t["currency"],
                payment_id=t.get("payment_id"),
            )
            for t in self.transfers
            if t["account_ref"] == account_ref and t["created_at"] >= since
        ]

    def lookup_transfer_reversals(self, transfer_ref: str) -> list[ReversalRecord]:
        return [
            ReversalRecord(
                ref=r["ref"], transfer_ref=r["transfer_ref"], amount_cents=r["amount_cents"]
            )
            for r in self.transfer_reversals
            if r["transfer_ref"] == transfer_ref
        ]


_fake_provider = FakePaymentProvider()


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.stripe_secret_key:
        from app.modules.payments.stripe_provider import StripePaymentProvider

        return StripePaymentProvider(settings.stripe_secret_key)
    if settings.environment == "prod":
        raise RuntimeError("Set TOOLBELT_STRIPE_SECRET_KEY before running payments in prod")
    return _fake_provider
