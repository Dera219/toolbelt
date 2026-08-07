"""Payments domain service: authorize on offer acceptance, capture + payout on
completion, release on cancellation, refunds via admin. Every money movement after
capture is recorded in the double-entry ledger."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.identity.models import User
from app.modules.jobs.models import Job
from app.modules.payments import ledger
from app.modules.payments.models import (
    BillingProfile,
    Payment,
    PaymentStatus,
    PayoutAccount,
)
from app.modules.payments.provider import ProviderError, get_payment_provider


def _fee_cents(amount_cents: int) -> int:
    return amount_cents * get_settings().platform_fee_bps // 10_000


# ---------------------------------------------------------------- accounts


def set_payment_method(db: Session, user: User, payment_method_ref: str) -> BillingProfile:
    provider = get_payment_provider()
    profile = db.get(BillingProfile, user.id)
    if profile is None:
        profile = BillingProfile(
            user_id=user.id, provider_customer_ref=provider.create_customer(user.email)
        )
        db.add(profile)
    provider.attach_payment_method(profile.provider_customer_ref, payment_method_ref)
    profile.default_payment_method_ref = payment_method_ref
    db.flush()
    return profile


def create_payout_account(db: Session, user: User) -> tuple[PayoutAccount, str]:
    if not user.can_work:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Workers only")
    account = db.get(PayoutAccount, user.id)
    if account is not None:
        return account, ""
    account_ref, onboarding_url = get_payment_provider().create_payout_account(user.email)
    account = PayoutAccount(user_id=user.id, provider_account_ref=account_ref)
    db.add(account)
    db.flush()
    return account, onboarding_url


# ---------------------------------------------------------------- job lifecycle hooks


def authorize_for_job(db: Session, customer: User, job: Job, price_cents: int) -> Payment:
    """Called inside accept_offer's transaction. Raising rolls the acceptance back."""
    billing = db.get(BillingProfile, customer.id)
    if billing is None or billing.default_payment_method_ref is None:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED, detail="Add a payment method before booking"
        )
    try:
        auth_ref = get_payment_provider().authorize(
            price_cents,
            job.currency,
            billing.provider_customer_ref,
            billing.default_payment_method_ref,
            metadata={"job_id": str(job.id)},
        )
    except ProviderError as exc:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail=f"Payment failed: {exc}")
    payment = Payment(
        job_id=job.id,
        customer_id=customer.id,
        worker_id=job.assigned_worker_id,
        amount_cents=price_cents,
        platform_fee_cents=_fee_cents(price_cents),
        currency=job.currency,
        status=PaymentStatus.AUTHORIZED,
        provider_auth_ref=auth_ref,
    )
    db.add(payment)
    db.flush()
    return payment


def capture_for_job(db: Session, job: Job) -> Payment | None:
    payment = db.scalar(select(Payment).where(Payment.job_id == job.id))
    if payment is None or payment.status != PaymentStatus.AUTHORIZED:
        return payment
    payment.provider_charge_ref = get_payment_provider().capture(payment.provider_auth_ref)
    payment.status = PaymentStatus.CAPTURED
    ledger.post_transaction(
        db,
        txn_key=f"capture:{payment.id}",
        currency=payment.currency,
        entries=[
            ("external:card_network", -payment.amount_cents),
            (f"worker:{payment.worker_id}", payment.worker_net_cents),
            ("platform:revenue", payment.platform_fee_cents),
        ],
        payment_id=payment.id,
    )
    _try_payout(db, payment)
    db.flush()
    return payment


def release_for_job(db: Session, job: Job) -> Payment | None:
    payment = db.scalar(select(Payment).where(Payment.job_id == job.id))
    if payment is None or payment.status != PaymentStatus.AUTHORIZED:
        return payment
    get_payment_provider().release(payment.provider_auth_ref)
    payment.status = PaymentStatus.RELEASED
    db.flush()
    return payment


# ---------------------------------------------------------------- payouts


def _try_payout(db: Session, payment: Payment) -> None:
    if payment.status != PaymentStatus.CAPTURED:
        return
    account = db.get(PayoutAccount, payment.worker_id)
    if account is None or not account.payouts_enabled:
        return  # funds stay on the worker's ledger balance until onboarding completes
    payment.provider_payout_ref = get_payment_provider().transfer(
        account.provider_account_ref,
        payment.worker_net_cents,
        payment.currency,
        metadata={"payment_id": str(payment.id)},
    )
    ledger.post_transaction(
        db,
        txn_key=f"payout:{payment.id}",
        currency=payment.currency,
        entries=[
            (f"worker:{payment.worker_id}", -payment.worker_net_cents),
            ("external:payouts", payment.worker_net_cents),
        ],
        payment_id=payment.id,
    )
    payment.status = PaymentStatus.PAID_OUT


def flush_pending_payouts(db: Session, worker_id: int) -> int:
    """Pay out any captured-but-unpaid payments (e.g. right after onboarding)."""
    payments = db.scalars(
        select(Payment).where(
            Payment.worker_id == worker_id, Payment.status == PaymentStatus.CAPTURED
        )
    )
    count = 0
    for payment in payments:
        _try_payout(db, payment)
        count += 1
    db.flush()
    return count


# ---------------------------------------------------------------- refunds


def refund_payment(db: Session, payment_id: int, amount_cents: int | None) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payment not found")
    if payment.status not in (PaymentStatus.CAPTURED, PaymentStatus.PAID_OUT):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Payment is not refundable")
    remaining = payment.amount_cents - payment.refunded_cents
    amount = amount_cents if amount_cents is not None else remaining
    if not 0 < amount <= remaining:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Refund must be between 1 and {remaining} cents",
        )
    get_payment_provider().refund(payment.provider_charge_ref, amount)
    # Worker and platform give back proportional shares; rounding lands on platform.
    worker_share = amount * payment.worker_net_cents // payment.amount_cents
    platform_share = amount - worker_share
    entries = [("external:card_network", amount), (f"worker:{payment.worker_id}", -worker_share)]
    if platform_share:
        entries.append(("platform:revenue", -platform_share))
    ledger.post_transaction(
        db,
        txn_key=f"refund:{payment.id}:{payment.refunded_cents + amount}",
        currency=payment.currency,
        entries=entries,
        payment_id=payment.id,
    )
    payment.refunded_cents += amount
    if payment.refunded_cents == payment.amount_cents:
        payment.status = PaymentStatus.REFUNDED
    db.flush()
    return payment


# ---------------------------------------------------------------- queries


def get_payment_for_job(db: Session, viewer: User, job: Job) -> Payment:
    if viewer.id not in (job.customer_id, job.assigned_worker_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not a party to this job")
    payment = db.scalar(select(Payment).where(Payment.job_id == job.id))
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No payment for this job")
    return payment
