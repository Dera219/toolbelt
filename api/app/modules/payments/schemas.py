from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.payments.models import PaymentStatus


class PaymentMethodIn(BaseModel):
    payment_method_ref: str = Field(min_length=1, max_length=64)


class CardSetupOut(BaseModel):
    """Native payment-sheet parameters. Publishable key is intentionally public."""

    setup_ref: str
    setup_intent_client_secret: str
    customer_ephemeral_key_secret: str
    customer_ref: str
    publishable_key: str | None = None


class ConfirmCardIn(BaseModel):
    """Which setup completed. Optional: older clients fall back to a lookup."""

    setup_ref: str | None = Field(default=None, max_length=200)


class CardSetupSessionIn(BaseModel):
    return_url: str = Field(min_length=1, max_length=500)


class CardSetupSessionOut(BaseModel):
    url: str


class BillingProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    default_payment_method_ref: str | None


class PayoutAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    provider_account_ref: str
    payouts_enabled: bool


class PayoutAccountCreatedOut(PayoutAccountOut):
    onboarding_url: str


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    customer_id: int
    worker_id: int
    amount_cents: int
    platform_fee_cents: int
    worker_net_cents: int
    refunded_cents: int
    currency: str
    status: PaymentStatus
    created_at: datetime


class BalanceOut(BaseModel):
    balance_cents: int
    currency: str


class RefundIn(BaseModel):
    amount_cents: int | None = Field(default=None, gt=0)


class TrialBalanceOut(BaseModel):
    total_cents: int
    balanced: bool


class ReconcileIn(BaseModel):
    """Parameters for a reconciliation sweep.

    `dry_run` defaults to True for the same reason the CLI does: the first run
    against production must be incapable of changing anything, and a default that
    writes is a default that gets triggered by a curious click.
    """

    dry_run: bool = True
    # Rows younger than this are still plausibly in flight; judging them races
    # the live call. Zero is allowed because the tests need it, but it is never
    # the right value against production.
    older_than_minutes: int = Field(default=15, ge=0, le=10_080)
    limit: int = Field(default=200, ge=1, le=1000)


class ReconcileOutcomeOut(BaseModel):
    key: str
    operation: str
    payment_id: int | None
    resolution: str
    provider_ref: str | None
    detail: str
    # Non-null means the journal and the local business state disagree about
    # money. Nothing is repaired automatically — this is the handoff to a human.
    discrepancy: str | None
    written: bool


class ReconcileReportOut(BaseModel):
    dry_run: bool
    grace_minutes: int
    scanned: int
    succeeded: int
    failed: int
    unknown: int
    # Pending rows too young to judge. Distinct from "nothing to do".
    in_grace_period: int
    discrepancy_count: int
    outcomes: list[ReconcileOutcomeOut]
