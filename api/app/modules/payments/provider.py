"""Payment provider seam. All money-rail operations go through this interface;
the domain logic (service.py, ledger.py) never imports a provider SDK directly.

Dev/test: FakePaymentProvider (in-process, inspectable).
Prod: StripePaymentProvider (stripe_provider.py), selected when a key is configured.
"""

from typing import Protocol

from app.core.config import get_settings


class ProviderError(Exception):
    """A money operation was declined or failed at the provider."""


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

    def authorize(
        self, amount_cents: int, currency: str, customer_ref: str, payment_method_ref: str,
        metadata: dict,
    ) -> str: ...
    def capture(self, auth_ref: str) -> str: ...
    def release(self, auth_ref: str) -> None: ...
    def refund(self, charge_ref: str, amount_cents: int) -> str: ...
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


class FakePaymentProvider:
    """In-process provider for dev and tests. Records every call; supports failure
    injection so decline paths are testable."""

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
        self.transfers: list[dict] = []
        self._transfer_keys: dict[str, str] = {}
        self.fail_next_transfer = False

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
        metadata: dict,
    ) -> str:
        if self.fail_next_authorize:
            self.fail_next_authorize = False
            raise ProviderError("Card declined")
        ref = self._ref("auth")
        self.authorizations.append(
            {"ref": ref, "amount_cents": amount_cents, "currency": currency, **metadata}
        )
        return ref

    def capture(self, auth_ref: str) -> str:
        self.captures.append(auth_ref)
        return auth_ref.replace("auth_", "ch_")

    def release(self, auth_ref: str) -> None:
        self.releases.append(auth_ref)

    def refund(self, charge_ref: str, amount_cents: int) -> str:
        self.refunds.append((charge_ref, amount_cents))
        return self._ref("re")

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
            {"ref": ref, "account_ref": account_ref, "amount_cents": amount_cents, **metadata}
        )
        return ref


_fake_provider = FakePaymentProvider()


def get_payment_provider() -> PaymentProvider:
    settings = get_settings()
    if settings.stripe_secret_key:
        from app.modules.payments.stripe_provider import StripePaymentProvider

        return StripePaymentProvider(settings.stripe_secret_key)
    if settings.environment == "prod":
        raise RuntimeError("Set TOOLBELT_STRIPE_SECRET_KEY before running payments in prod")
    return _fake_provider
