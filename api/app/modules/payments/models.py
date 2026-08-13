import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.identity.models import utcnow


class PaymentStatus(str, enum.Enum):
    AUTHORIZED = "authorized"  # customer's card held at offer acceptance
    CAPTURED = "captured"  # job completed, funds taken; payout pending
    PAID_OUT = "paid_out"  # worker's net transferred
    RELEASED = "released"  # authorization voided (job cancelled before work)
    REFUNDED = "refunded"  # fully refunded after capture


class BillingProfile(Base):
    """Customer-side payment identity: provider customer + default payment method.
    The client SDK tokenizes the card; the API only ever sees provider refs."""

    __tablename__ = "billing_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider_customer_ref: Mapped[str] = mapped_column(String(64))
    default_payment_method_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PayoutAccount(Base):
    """Worker-side connected account (Stripe Connect Express). payouts_enabled flips
    via provider webhook once onboarding/KYC completes."""

    __tablename__ = "payout_accounts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider_account_ref: Mapped[str] = mapped_column(String(64), unique=True)
    payouts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    platform_fee_cents: Mapped[int] = mapped_column(Integer)
    refunded_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), index=True)
    provider_auth_ref: Mapped[str] = mapped_column(String(64))
    provider_charge_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider_payout_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Bumped only when a payout attempt fails *definitively*. The provider caches
    # responses per idempotency key — including errors — so a key reused after a
    # failure replays that failure until the cache expires, and the worker never
    # gets paid. The counter gives each genuine retry a fresh key.
    payout_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def worker_net_cents(self) -> int:
        return self.amount_cents - self.platform_fee_cents


class LedgerEntry(Base):
    """Double-entry ledger (ARCHITECTURE.md §5). Entries are immutable and only ever
    written through ledger.post_transaction, which enforces that each transaction's
    entries sum to zero. Balances are derived by summing an account, never stored.

    Accounts:
      external:card_network  money entering/leaving via customer cards
      external:payouts       money leaving to workers' bank accounts
      worker:{user_id}       our liability to a worker (their balance with us)
      platform:revenue       earned platform fees
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("txn_key", "account", name="uq_ledger_txn_account"),
        Index("ix_ledger_account", "account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txn_key: Mapped[str] = mapped_column(String(64), index=True)
    account: Mapped[str] = mapped_column(String(64))
    amount_cents: Mapped[int] = mapped_column(Integer)  # signed; txn sums to zero
    currency: Mapped[str] = mapped_column(String(3))
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    """Processed provider webhook events — the idempotency record."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # provider event id
    event_type: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
