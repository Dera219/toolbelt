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

import stripe

from app.core.config import get_settings

# Pinned by @stripe/stripe-react-native; the ephemeral key it receives must
# be issued for the same API version or the sheet refuses to load.
MOBILE_SDK_API_VERSION = "2024-06-20"
from app.modules.payments.provider import ProviderError


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
        metadata: dict,
    ) -> str:
        try:
            intent = self._client.payment_intents.create(
                params={
                    "amount": amount_cents,
                    "currency": currency.lower(),
                    "customer": customer_ref,
                    "payment_method": payment_method_ref,
                    "capture_method": "manual",
                    "confirm": True,
                    "off_session": True,
                    "metadata": metadata,
                }
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        if intent.status != "requires_capture":
            raise ProviderError(f"Authorization not completed (status={intent.status})")
        return intent.id

    def capture(self, auth_ref: str) -> str:
        try:
            intent = self._client.payment_intents.capture(auth_ref)
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
        return intent.latest_charge or auth_ref

    def release(self, auth_ref: str) -> None:
        try:
            self._client.payment_intents.cancel(auth_ref)
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc

    def refund(self, charge_ref: str, amount_cents: int) -> str:
        try:
            refund = self._client.refunds.create(
                params={"charge": charge_ref, "amount": amount_cents}
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc
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
            raise ProviderError(str(exc)) from exc
        return transfer.id
