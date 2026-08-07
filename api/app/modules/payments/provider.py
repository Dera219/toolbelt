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
    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> None: ...
    def create_payout_account(self, email: str) -> tuple[str, str]:
        """Returns (account_ref, onboarding_url)."""
        ...

    def authorize(
        self, amount_cents: int, currency: str, customer_ref: str, payment_method_ref: str,
        metadata: dict,
    ) -> str: ...
    def capture(self, auth_ref: str) -> str: ...
    def release(self, auth_ref: str) -> None: ...
    def refund(self, charge_ref: str, amount_cents: int) -> str: ...
    def transfer(self, account_ref: str, amount_cents: int, currency: str, metadata: dict) -> str: ...


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
        self.authorizations: list[dict] = []
        self.captures: list[str] = []
        self.releases: list[str] = []
        self.refunds: list[tuple[str, int]] = []
        self.transfers: list[dict] = []

    def _ref(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_fake_{self._seq}"

    def create_customer(self, email: str) -> str:
        ref = self._ref("cus")
        self.customers.append(ref)
        return ref

    def attach_payment_method(self, customer_ref: str, payment_method_ref: str) -> None:
        self.attached.append((customer_ref, payment_method_ref))

    def create_payout_account(self, email: str) -> tuple[str, str]:
        ref = self._ref("acct")
        self.accounts.append(ref)
        return ref, f"https://onboarding.fake/{ref}"

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

    def transfer(self, account_ref: str, amount_cents: int, currency: str, metadata: dict) -> str:
        ref = self._ref("tr")
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
