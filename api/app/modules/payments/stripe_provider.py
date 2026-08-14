"""Stripe implementation of the PaymentProvider interface (Connect Express).

Flow mapping:
  authorize  -> PaymentIntent, capture_method=manual, confirmed off-session with the
                customer's saved default payment method (tokenized by the mobile SDK)
  capture    -> PaymentIntent.capture
  release    -> PaymentIntent.cancel
  refund     -> Refund against the captured intent
  transfer   -> Transfer to the worker's connected account

Connected accounts use **Accounts v2** (POST /v2/core/accounts). Accounts v1 is
rejected for new Connect integrations: "Stripe no longer recommends Accounts v1
for new Connect integrations." The v2 shape differs — capabilities live under
configuration.recipient.capabilities.stripe_balance.stripe_transfers, and
defaults.responsibilities must state who collects fees and who absorbs losses.

Selected by get_payment_provider() when TOOLBELT_STRIPE_SECRET_KEY is set. Exercised
against Stripe test mode; the test suite covers the domain via FakePaymentProvider.
"""

from datetime import datetime

import stripe

from app.core.config import get_settings

# Pinned by @stripe/stripe-react-native; the ephemeral key it receives must
# be issued for the same API version or the sheet refuses to load.
MOBILE_SDK_API_VERSION = "2024-06-20"
from app.modules.payments.provider import (
    AuthorizationRecord,
    AuthorizationState,
    ProviderError,
    RefundRecord,
    ReversalRecord,
    TransferRecord,
)

# How many objects a single reconciliation read will pull back. Stripe's list
# endpoints cap at 100 per page and the sweeper deliberately does not paginate:
# a customer with more than 100 authorizations inside the lookback window, or a
# charge with more than 100 refunds, is not a case to silently half-answer. The
# sweeper reports "cannot tell" instead — see `_page_is_complete` callers.
LOOKUP_PAGE_SIZE = 100

# Stripe PaymentIntent status → what the sweeper needs to know. Anything absent
# from this map is IN_FLIGHT: the provider has not finished deciding, so a
# reconciler that ruled either way would be guessing.
_INTENT_STATE = {
    "requires_capture": AuthorizationState.HELD,
    "succeeded": AuthorizationState.CAPTURED,
    "canceled": AuthorizationState.CANCELLED,
    # No hold was ever placed: the card was declined or confirmation never
    # completed. Money did not move and never will under this object.
    "requires_payment_method": AuthorizationState.NO_HOLD,
    "requires_confirmation": AuthorizationState.NO_HOLD,
}


def _metadata(obj) -> dict:
    """An object's metadata as a plain dict.

    `intent.metadata` is a StripeObject, not a dict. It has no `.get`, and
    `dict(...)` on it raises KeyError. Calling `.get` on it is the obvious
    mistake and it is invisible offline — every fake hands back a real dict — so
    it survives the whole unit suite and blows up the first time reconciliation
    runs against production. Normalizing once, here, is what keeps the reads
    honest.
    """
    metadata = getattr(obj, "metadata", None)
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    return metadata.to_dict()


def _provider_error(exc: stripe.StripeError) -> ProviderError:
    """Classify a Stripe failure.

    An InvalidRequestError or CardError means Stripe evaluated the request and
    refused it — nothing moved, so a retry under a fresh idempotency key is
    safe. A connection or generic API error leaves the outcome unknown, and the
    retry must reuse the original key so Stripe deduplicates it.

    An IdempotencyError is neither. Stripe raises it — HTTP 400, type
    `idempotency_error`, "Keys for idempotent requests can only be used with the
    same parameters they were first used with" — when a key comes back with a
    different request body. Reaching it means an earlier call under this key was
    accepted and this one asks for something else, so an operator has to know
    that money may already have moved. `definitive` stays False deliberately:
    the caller must not read this as "nothing happened, mint a fresh key",
    because a fresh key is exactly what would send the second payment.
    """
    if isinstance(exc, stripe.IdempotencyError):
        return ProviderError(
            "Stripe refused this call: its idempotency key was already used with "
            f"different parameters ({exc}). A call has already gone out for this "
            "operation at different terms — reconcile the payment against Stripe "
            "before retrying, and retry at the original amount to replay the first "
            "call safely.",
            definitive=False,
        )
    definitive = isinstance(exc, (stripe.InvalidRequestError, stripe.CardError))
    return ProviderError(str(exc), definitive=definitive)


class StripePaymentProvider:
    def __init__(self, secret_key: str) -> None:
        self._client = stripe.StripeClient(secret_key)

    def create_customer(self, email: str) -> str:
        try:
            customer = self._client.customers.create(params={"email": email})
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return customer.id

    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> str:
        """Attach and return the resulting PaymentMethod id.

        Test shorthands like "pm_card_visa" are aliases: each use resolves to a
        brand-new PaymentMethod. Re-sending the alias to customers.update would
        mint a second, unattached one and fail — so everything downstream must
        use the id attach() actually returned.
        """
        try:
            attached = self._client.payment_methods.attach(
                payment_method_ref, params={"customer": customer_ref}
            )
            self._client.customers.update(
                customer_ref,
                params={"invoice_settings": {"default_payment_method": attached.id}},
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return attached.id

    def create_payout_account(self, email: str) -> tuple[str, str]:
        """Create a connected account for a worker and return (id, onboarding_url).

        Uses Accounts **v2** (`POST /v2/core/accounts`). Accounts v1 is rejected
        for new Connect integrations — see the migration note in the module
        docstring.

        The account is created with only a country and entity type; the worker
        supplies their legal name, address, and bank details through Stripe's
        hosted onboarding flow, which is the whole point of the returned link.
        """
        settings = get_settings()
        try:
            account = self._client.v2.core.accounts.create(
                params={
                    "contact_email": email,
                    "identity": {
                        "country": settings.connect_country,
                        "entity_type": "individual",
                    },
                    "configuration": {
                        "recipient": {
                            "capabilities": {
                                "stripe_balance": {"stripe_transfers": {"requested": True}}
                            }
                        }
                    },
                    # Who owes fees and who eats losses. "application" means
                    # ToolBelt — correct here, because ToolBelt is the merchant
                    # of record and takes the 15% platform fee, so chargebacks
                    # land on the platform rather than on the worker.
                    "defaults": {
                        "currency": settings.default_currency,
                        "responsibilities": {
                            "fees_collector": "application",
                            "losses_collector": "application",
                        },
                    },
                    "dashboard": "express",
                    "include": ["configuration.recipient"],
                }
            )
            link = self._client.v2.core.account_links.create(
                params={
                    "account": account.id,
                    "use_case": {
                        "type": "account_onboarding",
                        "account_onboarding": {
                            "configurations": ["recipient"],
                            "refresh_url": f"{settings.public_base_url}/onboarding/refresh",
                            "return_url": f"{settings.public_base_url}/onboarding/done",
                        },
                    },
                }
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return account.id, link.url

    def payouts_enabled(self, account_ref: str) -> bool:
        """Ask Stripe whether transfers are live on this account.

        Polled rather than inferred from webhooks: onboarding completes in
        Stripe's hosted flow, and a laptop cannot receive the resulting
        account.updated callback without a tunnel. The webhook still works in
        production; this keeps the read path truthful everywhere.
        """
        try:
            account = self._client.v2.core.accounts.retrieve(
                account_ref, params={"include": ["configuration.recipient"]}
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        # Walk defensively: an account early in onboarding may omit any level of
        # this chain, and an AttributeError here would escape the StripeError
        # handler above and surface as a 500 instead of a clean provider error.
        node = getattr(account, "configuration", None)
        for attr in ("recipient", "capabilities", "stripe_balance", "stripe_transfers"):
            node = getattr(node, attr, None)
            if node is None:
                return False
        return getattr(node, "status", None) == "active"

    def create_card_setup(self, customer_ref: str) -> dict:
        """SetupIntent + ephemeral key for the native PaymentSheet.

        `usage="off_session"` matters: the saved card is charged later, when the
        customer accepts an offer and is not present. Without it Stripe may
        decline that charge for missing prior authorization.
        """
        try:
            intent = self._client.v1.setup_intents.create(
                params={
                    "customer": customer_ref,
                    "usage": "off_session",
                    "automatic_payment_methods": {"enabled": True},
                }
            )
            # The mobile SDK pins an API version; the ephemeral key must match it.
            ephemeral = self._client.v1.ephemeral_keys.create(
                params={"customer": customer_ref},
                options={"stripe_version": MOBILE_SDK_API_VERSION},
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return {
            "setup_ref": intent.id,
            "setup_intent_client_secret": intent.client_secret,
            "customer_ephemeral_key_secret": ephemeral.secret,
            "customer_ref": customer_ref,
        }

    def create_card_setup_session(self, customer_ref: str, return_url: str) -> str:
        """Stripe-hosted card entry — used on the web, where the native sheet
        does not exist. Card details go straight to Stripe, never through us."""
        try:
            session = self._client.v1.checkout.sessions.create(
                params={
                    "mode": "setup",
                    "customer": customer_ref,
                    # Required in setup mode: Stripe uses it to decide which
                    # payment methods to offer, even though nothing is charged.
                    "currency": get_settings().default_currency,
                    # The session id comes back so confirmation can resolve
                    # exactly what was saved instead of guessing from a list.
                    "success_url": f"{return_url}?card=saved&session_id={{CHECKOUT_SESSION_ID}}",
                    "cancel_url": f"{return_url}?card=cancelled",
                }
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return session.url

    def latest_payment_method(self, customer_ref: str) -> str | None:
        """Any attached payment method, newest first.

        Deliberately unfiltered by type: Stripe Checkout may save a Link
        payment method rather than a raw card, and filtering on type="card"
        silently returns nothing — the user sees "no card was saved" for a card
        that saved perfectly well.
        """
        try:
            methods = self._client.v1.payment_methods.list(
                params={"customer": customer_ref, "limit": 1}
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return methods.data[0].id if methods.data else None

    def payment_method_from_setup(self, setup_ref: str) -> str | None:
        """Exact resolution: ask what this specific setup saved."""
        try:
            if setup_ref.startswith("cs_"):
                session = self._client.v1.checkout.sessions.retrieve(setup_ref)
                setup_ref = session.setup_intent or ""
                if not setup_ref:
                    return None
            intent = self._client.v1.setup_intents.retrieve(setup_ref)
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        if intent.status != "succeeded":
            return None
        return intent.payment_method

    def set_default_payment_method(self, customer_ref: str, payment_method_ref: str) -> None:
        try:
            self._client.customers.update(
                customer_ref,
                params={"invoice_settings": {"default_payment_method": payment_method_ref}},
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc

    def onboarding_link(self, account_ref: str) -> str:
        settings = get_settings()
        try:
            link = self._client.v2.core.account_links.create(
                params={
                    "account": account_ref,
                    "use_case": {
                        "type": "account_onboarding",
                        "account_onboarding": {
                            "configurations": ["recipient"],
                            "refresh_url": f"{settings.public_base_url}/onboarding/refresh",
                            "return_url": f"{settings.public_base_url}/onboarding/done",
                        },
                    },
                }
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return link.url

    def authorize(
        self, amount_cents: int, currency: str, customer_ref: str, payment_method_ref: str,
        metadata: dict, idempotency_key: str,
    ) -> str:
        try:
            intent = self._client.v1.payment_intents.create(
                params={
                    "amount": amount_cents,
                    "currency": currency.lower(),
                    "customer": customer_ref,
                    "payment_method": payment_method_ref,
                    "capture_method": "manual",
                    "confirm": True,
                    "off_session": True,
                    "metadata": metadata,
                },
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if intent.status != "requires_capture":
            raise ProviderError(f"Authorization not completed (status={intent.status})")
        return intent.id

    def capture(self, auth_ref: str, idempotency_key: str) -> str:
        """Capture an authorization, tolerating a capture that already landed.

        Stripe can perform the capture and *then* fail the response — a
        transient error after the money moved. Retrying naively raises "already
        captured" forever while our records still say authorized, so the money
        is taken but never credited to anyone. Treat an already-succeeded intent
        as the success it is.

        The idempotency key handles the same race one layer earlier, inside
        Stripe's own 24-hour window; the retrieve below is what still works
        after the key is pruned.
        """
        try:
            intent = self._client.v1.payment_intents.capture(
                auth_ref, options={"idempotency_key": idempotency_key}
            )
        except stripe.StripeError as exc:
            try:
                intent = self._client.v1.payment_intents.retrieve(auth_ref)
            except stripe.StripeError:
                raise _provider_error(exc) from exc
            if intent.status != "succeeded":
                raise _provider_error(exc) from exc
        return intent.latest_charge or auth_ref

    def release(self, auth_ref: str, idempotency_key: str) -> None:
        try:
            self._client.v1.payment_intents.cancel(
                auth_ref, options={"idempotency_key": idempotency_key}
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc

    def refund(self, charge_ref: str, amount_cents: int, idempotency_key: str) -> str:
        """Refund a captured charge, keyed so a replay is a no-op.

        This call is made before the refund is recorded locally, so a failure
        anywhere later in the same transaction rolls the database back over a
        refund that already happened. Replaying the key returns the original
        refund instead of sending the customer their money a second time.

        The key covers the refund *generation*, not the amount (see
        `refund_idempotency_key`). A retry at a different amount therefore
        arrives at Stripe as the same key with a different body, and Stripe
        answers with an idempotency_error — mapped by `_provider_error` into a
        message that tells an operator to reconcile. Loud is the point: the
        alternative, a key that includes the amount, would quietly issue a
        second real refund.
        """
        try:
            refund = self._client.v1.refunds.create(
                params={"charge": charge_ref, "amount": amount_cents},
                options={"idempotency_key": idempotency_key},
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return refund.id

    def transfer(
        self, account_ref: str, amount_cents: int, currency: str, metadata: dict,
        idempotency_key: str,
    ) -> str:
        """Transfer to a connected account, keyed so a replay is a no-op.

        Stripe returns the original transfer when the same idempotency key is
        reused, which is what makes a retry after a rolled-back transaction safe
        rather than a second payout.
        """
        try:
            transfer = self._client.transfers.create(
                options={"idempotency_key": idempotency_key},
                params={
                    "amount": amount_cents,
                    "currency": currency.lower(),
                    "destination": account_ref,
                    "metadata": metadata,
                }
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return transfer.id

    def reverse_transfer(
        self, transfer_ref: str, amount_cents: int, idempotency_key: str,
    ) -> str:
        """Reverse (part of) a transfer, keyed so a replay is a no-op.

        Stripe pulls the amount back from the connected account's balance into
        the platform's; reusing the idempotency key returns the original
        reversal rather than collecting from the worker twice.
        """
        try:
            reversal = self._client.transfers.reversals.create(
                transfer_ref,
                options={"idempotency_key": idempotency_key},
                params={"amount": amount_cents},
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return reversal.id

    # ------------------------------------------------------------ reconciliation reads
    #
    # Everything below is a GET. Not one of these can create an object, which is
    # the property that makes them usable on a journal row whose outcome is
    # unknown. The alternative — replaying the original request under its
    # idempotency key — is a mutation whose result depends on whether Stripe
    # ever received the first attempt: inside the 24-hour key window a replay
    # returns the original object *if the key was consumed*, and creates a brand
    # new charge if it was not. "Was it consumed?" is exactly the question the
    # sweeper is trying to answer, so the replay cannot be part of answering it.

    @staticmethod
    def _authorization(intent) -> AuthorizationRecord:
        return AuthorizationRecord(
            ref=intent.id,
            state=_INTENT_STATE.get(intent.status, AuthorizationState.IN_FLIGHT),
            amount_cents=intent.amount,
            currency=intent.currency,
            customer_ref=intent.customer,
            payment_method_ref=intent.payment_method,
            charge_ref=intent.latest_charge,
            job_id=_metadata(intent).get("job_id"),
        )

    def lookup_authorization(self, auth_ref: str) -> AuthorizationRecord | None:
        """Retrieve one PaymentIntent. None when Stripe has no such object.

        A missing intent is answered as None rather than raised, because "there
        is nothing here" is a real finding for the sweeper: it is the difference
        between a capture that never went out and a capture whose outcome we
        cannot see.
        """
        try:
            intent = self._client.v1.payment_intents.retrieve(auth_ref)
        except stripe.InvalidRequestError:
            return None
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        return self._authorization(intent)

    def lookup_authorizations_for_customer(
        self, customer_ref: str, *, since: datetime
    ) -> list[AuthorizationRecord]:
        """Authorizations created for a customer since `since`.

        `list` rather than `search`: Stripe's Search API is eventually
        consistent ("Occasionally, propagation of new or updated data can be up
        to a minute behind"), and an index that has not caught up would report
        an authorization as absent — which the sweeper would read as "nothing
        moved" over a real hold on a customer's card. List reads are
        strongly consistent, so a miss here means a genuine miss.
        """
        try:
            page = self._client.v1.payment_intents.list(
                params={
                    "customer": customer_ref,
                    "created": {"gte": int(since.timestamp())},
                    "limit": LOOKUP_PAGE_SIZE,
                }
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if page.has_more:
            # Refusing to answer beats answering from a truncated page: the
            # sweeper's "failed" verdict means "nothing exists", and a page that
            # stopped early cannot support that claim.
            raise ProviderError(
                f"customer {customer_ref} has more than {LOOKUP_PAGE_SIZE} authorizations "
                "in the reconciliation window; refusing to judge from a partial page",
                definitive=True,
            )
        return [self._authorization(intent) for intent in page.data]

    def lookup_refunds(self, charge_ref: str) -> list[RefundRecord]:
        try:
            page = self._client.v1.refunds.list(
                params={"charge": charge_ref, "limit": LOOKUP_PAGE_SIZE}
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if page.has_more:
            raise ProviderError(
                f"charge {charge_ref} has more than {LOOKUP_PAGE_SIZE} refunds; refusing "
                "to judge from a partial page",
                definitive=True,
            )
        return [
            RefundRecord(
                ref=refund.id,
                charge_ref=charge_ref,
                amount_cents=refund.amount,
                # A refund Stripe later failed or cancelled put the money back
                # on our side. Counting it as money returned to the customer
                # would tell an operator a refund landed when it did not.
                settled=refund.status not in ("failed", "canceled"),
            )
            for refund in page.data
        ]

    def lookup_transfers_to(
        self, account_ref: str, *, since: datetime
    ) -> list[TransferRecord]:
        try:
            page = self._client.transfers.list(
                params={
                    "destination": account_ref,
                    "created": {"gte": int(since.timestamp())},
                    "limit": LOOKUP_PAGE_SIZE,
                }
            )
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if page.has_more:
            raise ProviderError(
                f"account {account_ref} received more than {LOOKUP_PAGE_SIZE} transfers in "
                "the reconciliation window; refusing to judge from a partial page",
                definitive=True,
            )
        return [
            TransferRecord(
                ref=transfer.id,
                account_ref=account_ref,
                amount_cents=transfer.amount,
                currency=transfer.currency,
                payment_id=_metadata(transfer).get("payment_id"),
            )
            for transfer in page.data
        ]

    def lookup_transfer_reversals(self, transfer_ref: str) -> list[ReversalRecord]:
        try:
            page = self._client.transfers.reversals.list(
                transfer_ref, params={"limit": LOOKUP_PAGE_SIZE}
            )
        except stripe.InvalidRequestError:
            # The transfer itself does not exist, so neither do its reversals.
            return []
        except stripe.StripeError as exc:
            raise _provider_error(exc) from exc
        if page.has_more:
            raise ProviderError(
                f"transfer {transfer_ref} has more than {LOOKUP_PAGE_SIZE} reversals; "
                "refusing to judge from a partial page",
                definitive=True,
            )
        return [
            ReversalRecord(
                ref=reversal.id, transfer_ref=transfer_ref, amount_cents=reversal.amount
            )
            for reversal in page.data
        ]
