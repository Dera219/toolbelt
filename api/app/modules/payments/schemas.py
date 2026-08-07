from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.payments.models import PaymentStatus


class PaymentMethodIn(BaseModel):
    payment_method_ref: str = Field(min_length=1, max_length=64)


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
