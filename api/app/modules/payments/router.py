import hashlib
import hmac
import json
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
    account = db.get(PayoutAccount, user.id)
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
