import hashlib
import hmac
import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import get_current_user, require_admin
from app.modules.identity.models import User
from app.modules.jobs.models import Job
from app.modules.payments import ledger, service
from app.modules.payments.models import PayoutAccount, WebhookEvent
from app.modules.payments.schemas import (
    BalanceOut,
    BillingProfileOut,
    CardSetupOut,
    CardSetupSessionIn,
    CardSetupSessionOut,
    ConfirmCardIn,
    PaymentMethodIn,
    PaymentOut,
    PayoutAccountCreatedOut,
    PayoutAccountOut,
    RefundIn,
    TrialBalanceOut,
)

router = APIRouter()

DbDep = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("/me/billing/profile", response_model=BillingProfileOut)
def get_billing_profile(user: CurrentUser, db: DbDep):
    from app.modules.payments.models import BillingProfile

    profile = db.get(BillingProfile, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No payment method on file")
    return profile


@router.post("/me/billing/card-setup", response_model=CardSetupOut)
def start_card_setup(user: CurrentUser, db: DbDep):
    """Native card entry. The app opens Stripe's payment sheet with these."""
    setup = service.start_card_setup(db, user)
    return CardSetupOut(**setup, publishable_key=get_settings().stripe_publishable_key)


@router.post("/me/billing/card-setup-session", response_model=CardSetupSessionOut)
def start_card_setup_session(body: CardSetupSessionIn, user: CurrentUser, db: DbDep):
    """Web card entry — a Stripe-hosted page the browser is redirected to."""
    return CardSetupSessionOut(url=service.start_card_setup_session(db, user, body.return_url))


@router.post("/me/billing/confirm-card", response_model=BillingProfileOut)
def confirm_card(user: CurrentUser, db: DbDep, body: ConfirmCardIn | None = None):
    """Called after the sheet or hosted page succeeds; records what was saved."""
    return service.confirm_card_setup(db, user, body.setup_ref if body else None)


@router.post("/me/payment-method", response_model=BillingProfileOut)
def set_payment_method(body: PaymentMethodIn, user: CurrentUser, db: DbDep):
    return service.set_payment_method(db, user, body.payment_method_ref)


@router.post("/me/payout-account", response_model=PayoutAccountCreatedOut)
def create_payout_account(user: CurrentUser, db: DbDep):
    account, onboarding_url = service.create_payout_account(db, user)
    return PayoutAccountCreatedOut(
        user_id=account.user_id,
        provider_account_ref=account.provider_account_ref,
        payouts_enabled=account.payouts_enabled,
        onboarding_url=onboarding_url,
    )


@router.get("/me/payout-account", response_model=PayoutAccountOut)
def get_payout_account(user: CurrentUser, db: DbDep):
    # Re-checks the provider when onboarding is still incomplete, so finishing
    # Stripe's hosted flow is reflected without waiting on a webhook.
    account = service.sync_payout_account(db, user)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No payout account")
    return account


@router.get("/me/balance", response_model=BalanceOut)
def my_balance(user: CurrentUser, db: DbDep):
    return BalanceOut(
        balance_cents=ledger.account_balance(db, f"worker:{user.id}"), currency=user.currency
    )


@router.get("/jobs/{job_id}/payment", response_model=PaymentOut)
def job_payment(job_id: int, user: CurrentUser, db: DbDep):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return service.get_payment_for_job(db, user, job)


@router.post("/webhooks/payments")
async def payments_webhook(request: Request, db: DbDep):
    raw = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    expected = hmac.new(
        get_settings().payments_webhook_secret.encode(), raw, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    try:
        event = json.loads(raw)
        event_id, event_type, data = event["id"], event["type"], event.get("data", {})
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Malformed event")

    if db.get(WebhookEvent, event_id) is not None:
        return {"status": "duplicate"}
    db.add(WebhookEvent(id=event_id, event_type=event_type))

    if event_type == "account.updated":
        account_ref = data.get("account_ref")
        account = (
            db.query(PayoutAccount).filter_by(provider_account_ref=account_ref).one_or_none()
        )
        if account is not None:
            account.payouts_enabled = bool(data.get("payouts_enabled", False))
            if account.payouts_enabled:
                service.flush_pending_payouts(db, account.user_id)
    # Unknown event types are recorded and acknowledged — providers retry on non-2xx.
    return {"status": "processed"}


STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300


def _verify_stripe_signature(raw: bytes, header: str, secret: str) -> bool:
    """Verify Stripe's `Stripe-Signature` scheme: `t=<unix seconds>,v1=<hex>`,
    where the HMAC-SHA256 is computed over b"{t}.{raw body}". The timestamp
    bounds how long a captured delivery can be replayed; multiple v1 entries
    appear while signatures from a rotated secret are still being sent.
    """
    timestamp: str | None = None
    candidates: list[str] = []
    for element in header.split(","):
        key, sep, value = element.strip().partition("=")
        if not sep:
            continue
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > STRIPE_SIGNATURE_TOLERANCE_SECONDS:
        return False
    signed_payload = timestamp.encode() + b"." + raw
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(candidate, expected) for candidate in candidates)


def _stripe_transfers_active(account: dict) -> bool:
    """Payouts-enabled criterion for a v2 core account payload — the same
    configuration.recipient.capabilities.stripe_balance.stripe_transfers chain
    StripePaymentProvider.payouts_enabled polls, walked defensively because an
    account early in onboarding may omit any level of it. Falls back to the
    flat v1 `payouts_enabled` bool so a v1-shaped payload still translates.
    """
    node: object = account.get("configuration")
    for key in ("recipient", "capabilities", "stripe_balance", "stripe_transfers"):
        if not isinstance(node, dict):
            break
        node = node.get(key)
    else:
        if isinstance(node, dict) and node.get("status") == "active":
            return True
    return account.get("payouts_enabled") is True


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: DbDep):
    """Stripe's delivery path for the onboarding signal /webhooks/payments
    models internally. Latency optimization only: polling
    (service.sync_payout_account) keeps payout state correct without it, so a
    missed delivery costs nothing but time.
    """
    secret = get_settings().stripe_webhook_secret
    if not secret:
        # Fail loudly rather than accept unverifiable events: registering the
        # endpoint in Stripe before configuring the secret should surface as
        # delivery errors in the Stripe dashboard, not silently dropped signals.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="Stripe webhook secret not configured"
        )
    raw = await request.body()
    if not _verify_stripe_signature(raw, request.headers.get("Stripe-Signature", ""), secret):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    try:
        event = json.loads(raw)
        event_id, event_type = event["id"], event["type"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Malformed event")
    if not isinstance(event_id, str) or not isinstance(event_type, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Malformed event")

    if db.get(WebhookEvent, event_id) is not None:
        return {"status": "duplicate"}
    db.add(WebhookEvent(id=event_id, event_type=event_type))

    data = event.get("data")
    account_obj = data.get("object") if isinstance(data, dict) else None
    if event_type == "account.updated" and isinstance(account_obj, dict):
        account = (
            db.query(PayoutAccount)
            .filter_by(provider_account_ref=account_obj.get("id"))
            .one_or_none()
        )
        if account is not None:
            account.payouts_enabled = _stripe_transfers_active(account_obj)
            if account.payouts_enabled:
                service.flush_pending_payouts(db, account.user_id)
    # Unknown event types are recorded and acknowledged — Stripe retries on non-2xx.
    return {"status": "processed"}


@router.post("/admin/payments/{payment_id}/refund", response_model=PaymentOut)
def refund_payment(
    payment_id: int,
    body: RefundIn,
    db: DbDep,
    _admin: Annotated[User, Depends(require_admin)],
):
    return service.refund_payment(db, payment_id, body.amount_cents)


@router.get("/admin/ledger/trial-balance", response_model=TrialBalanceOut)
def ledger_trial_balance(db: DbDep, _admin: Annotated[User, Depends(require_admin)]):
    total = ledger.trial_balance(db)
    return TrialBalanceOut(total_cents=total, balanced=total == 0)
