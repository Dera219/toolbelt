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
    Text,
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


class ProviderCallStatus(str, enum.Enum):
    PENDING = "pending"  # recorded before the call; outcome unknown
    SUCCEEDED = "succeeded"  # the provider accepted it and returned provider_ref
    FAILED = "failed"  # the last attempt raised; see `error`


class ProviderCall(Base):
    """Every money-moving request we have made to the payment provider.

    The ledger records what money *did*. This records what we *asked the
    provider to do*, and it is the other half of the same story: a provider call
    is an irreversible external effect made from inside a database transaction
    that is not yet durable, so a failure after the call rolls the database back
    while the money has already moved. Without this table the retry has no
    memory of the first attempt and pays a second time.

    Rows are written through app.modules.payments.journal, which commits them on
    their own connection so they survive the rollback they exist to outlive.

    `payment_id` is deliberately a plain integer and NOT a foreign key. The
    journal must be writable at moments when the payment row is either not yet
    created (authorize) or only visible inside the uncommitted business
    transaction; a real constraint would make the referential check depend on
    that transaction's visibility and could refuse — or on Postgres block on —
    the very write that is supposed to make the call safe. Refusing to pay
    someone because of a referential-integrity artifact is a worse failure than
    an unenforced reference.
    """

    __tablename__ = "provider_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The natural key. Unique so two processes racing the same logical operation
    # resolve to one row rather than two calls.
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    operation: Mapped[str] = mapped_column(String(32))
    payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # sha256 of the call's parameters. A key that comes back with a different
    # fingerprint means someone is retrying the same logical operation with
    # different terms, which is the shape of a double-spend.
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[ProviderCallStatus] = mapped_column(Enum(ProviderCallStatus), index=True)
    provider_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WebhookEvent(Base):
    """Processed provider webhook events — the idempotency record."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # provider event id
    event_type: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
