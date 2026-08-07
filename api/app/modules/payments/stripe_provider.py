"""Stripe implementation of the PaymentProvider interface (Connect Express).

Flow mapping:
  authorize  -> PaymentIntent, capture_method=manual, confirmed off-session with the
                customer's saved default payment method (tokenized by the mobile SDK)
  capture    -> PaymentIntent.capture
  release    -> PaymentIntent.cancel
  refund     -> Refund against the captured intent
  transfer   -> Transfer to the worker's Express account

Selected by get_payment_provider() when TOOLBELT_STRIPE_SECRET_KEY is set. Exercised
against Stripe test mode; the test suite covers the domain via FakePaymentProvider.
"""

import stripe

from app.modules.payments.provider import ProviderError


class StripePaymentProvider:
    def __init__(self, secret_key: str) -> None:
        self._client = stripe.StripeClient(secret_key)

    def create_customer(self, email: str) -> str:
        customer = self._client.customers.create(params={"email": email})
        return customer.id

    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> None:
        try:
            self._client.payment_methods.attach(
                payment_method_ref, params={"customer": customer_ref}
            )
            self._client.customers.update(
                customer_ref,
                params={"invoice_settings": {"default_payment_method": payment_method_ref}},
            )
        except stripe.StripeError as exc:
            raise ProviderError(str(exc)) from exc

    def create_payout_account(self, email: str) -> tuple[str, str]:
        account = self._client.accounts.create(
            params={"type": "express", "email": email, "capabilities": {
                "transfers": {"requested": True}}}
        )
        link = self._client.account_links.create(
            params={
                "account": account.id,
                "type": "account_onboarding",
                "refresh_url": "https://toolbelt.example/onboarding/refresh",
                "return_url": "https://toolbelt.example/onboarding/done",
            }
        )
        return account.id, link.url

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

    def transfer(self, account_ref: str, amount_cents: int, currency: str, metadata: dict) -> str:
        try:
            transfer = self._client.transfers.create(
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
