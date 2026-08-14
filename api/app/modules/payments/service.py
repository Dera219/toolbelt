"""Payments domain service: authorize on offer acceptance, capture + payout on
completion, release on cancellation, refunds via admin. Every money movement after
capture is recorded in the double-entry ledger."""

import logging

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.identity.models import User
from app.modules.jobs.models import Job
from app.modules.payments import ledger
from app.modules.payments.journal import ProviderCallConflict, execute_provider_call
from app.modules.payments.models import (
    BillingProfile,
    Payment,
    PaymentStatus,
    PayoutAccount,
)
from app.modules.payments.provider import ProviderError, get_payment_provider

logger = logging.getLogger(__name__)


def _fee_cents(amount_cents: int) -> int:
    return amount_cents * get_settings().platform_fee_bps // 10_000


# ---------------------------------------------------------------- idempotency keys
#
# One key per logical money movement, built here and nowhere else. A caller that
# rebuilds one of these strings by hand silently stops deduplicating the moment
# the format changes, and the only symptom is somebody paid twice.
#
# Each string is the provider's idempotency key and the journal's natural key at
# once (journal.py), so the local record and the provider's own deduplication
# can never disagree about what "the same call" means.
#
# The authorize, payout and reversal spellings are already in flight against
# live Stripe and are kept exactly as they were. Renaming a key that a pending
# call was made under re-arms the double-spend it exists to prevent: the
# provider sees a brand-new request and moves the money again. The three keys
# introduced with the journal say `-call` to stay visibly distinct from the
# ledger transaction keys, which share the same operation names but a different
# namespace and — for refunds — a deliberately different suffix.


def authorize_idempotency_key(job_id: int, amount_cents: int, payment_method_ref: str) -> str:
    """Keyed by job *and* by the terms of the charge.

    An identical retry returns the original hold instead of placing a second
    one; a genuinely different charge — a new amount after a new offer, or a
    different card — gets its own key. Keying on the job alone makes the
    provider reject that second case outright, because a key may only be reused
    with identical parameters.
    """
    return f"authorize:{job_id}:{amount_cents}:{payment_method_ref[-14:]}"


def capture_idempotency_key(payment: Payment) -> str:
    """One capture per payment, ever. The ledger's transaction for the same
    event is `capture:{payment.id}`; this is a different namespace and says so."""
    return f"capture-call:{payment.id}"


def release_idempotency_key(payment: Payment) -> str:
    """One cancellation per payment, ever."""
    return f"release-call:{payment.id}"


def payout_idempotency_key(payment: Payment) -> str:
    """The key a payout attempt is made under.

    The attempt counter is part of it because the provider caches failures too:
    a key reused after a definitive rejection replays that rejection until the
    cache expires, and the worker never gets paid.
    """
    return f"payout:{payment.id}#{payment.payout_attempts}"


def refund_idempotency_key(payment: Payment) -> str:
    """Keyed on the refund *generation* — what was already refunded before this
    one — and deliberately NOT on the amount being refunded now.

    Including the amount is the tempting version and it is the dangerous one. A
    retry at a different amount would mint a different key, and a different key
    is a second real refund on top of a first one that may well have landed.
    That is exactly the trap the transfer reversals fell into: a failed 5000
    refund followed by a 3000 retry double-collected, because the two attempts
    keyed differently.

    Keying on the generation instead makes the two retries meet:
      - the same amount replays the original refund and moves nothing;
      - a different amount reuses a key with a different body, which the journal
        refuses outright and which Stripe would reject with an idempotency_error
        anyway. An operator gets told to reconcile instead of a customer getting
        refunded twice.

    The ledger's transaction key for the same refund is
    `refund:{payment.id}:{refunded_cents + amount}` — the cumulative total
    *after* the refund. Different namespace, different suffix, different prefix,
    so the two can never be confused for one another.
    """
    return f"refund-call:{payment.id}:{payment.refunded_cents}"


def reversal_idempotency_key(payment: Payment, amount_cents: int) -> str:
    """Claw-backs key on the cumulative refunded total, matching the ledger
    transaction that records them."""
    return f"reverse:{payment.id}:{payment.refunded_cents + amount_cents}"


# ---------------------------------------------------------------- accounts


def set_payment_method(db: Session, user: User, payment_method_ref: str) -> BillingProfile:
    provider = get_payment_provider()
    profile = db.get(BillingProfile, user.id)
    if profile is None:
        profile = BillingProfile(
            user_id=user.id, provider_customer_ref=provider.create_customer(user.email)
        )
        db.add(profile)
        db.flush()
    try:
        attached_ref = provider.attach_payment_method(
            profile.provider_customer_ref, payment_method_ref
        )
    except ProviderError as exc:
        # A rejected card is the user's problem to fix, not a server fault.
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, detail=f"Card rejected: {exc}")
    # Store what the provider actually attached, not what the client submitted.
    profile.default_payment_method_ref = attached_ref
    db.flush()
    return profile


def _billing_profile(db: Session, user: User) -> BillingProfile:
    """Fetch or create the provider-side customer for this user."""
    profile = db.get(BillingProfile, user.id)
    if profile is None:
        profile = BillingProfile(
            user_id=user.id,
            provider_customer_ref=get_payment_provider().create_customer(user.email),
        )
        db.add(profile)
        db.flush()
    return profile


def start_card_setup(db: Session, user: User) -> dict:
    """Native path: hand the app what its payment sheet needs. Card details go
    from the device straight to the provider — they never touch this server."""
    profile = _billing_profile(db, user)
    try:
        return get_payment_provider().create_card_setup(profile.provider_customer_ref)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Could not start setup: {exc}")


def start_card_setup_session(db: Session, user: User, return_url: str) -> str:
    """Web path: a provider-hosted card page, because the native sheet has no
    browser equivalent."""
    profile = _billing_profile(db, user)
    try:
        return get_payment_provider().create_card_setup_session(
            profile.provider_customer_ref, return_url
        )
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Could not start setup: {exc}")


def confirm_card_setup(db: Session, user: User, setup_ref: str | None = None) -> BillingProfile:
    """Record whatever card the user actually saved.

    Called after the sheet or hosted page reports success. We ask the provider
    what is on the customer rather than trusting the client to tell us — a
    client-supplied payment method id could point at someone else's card.
    """
    profile = db.get(BillingProfile, user.id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No billing profile")
    provider = get_payment_provider()
    try:
        # Exact when the client tells us which setup completed; the list is a
        # fallback for clients that cannot report one.
        payment_method = (
            provider.payment_method_from_setup(setup_ref) if setup_ref else None
        ) or provider.latest_payment_method(profile.provider_customer_ref)
        if payment_method is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="No payment method was saved. Try adding it again.",
            )
        provider.set_default_payment_method(profile.provider_customer_ref, payment_method)
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Could not confirm card: {exc}")
    profile.default_payment_method_ref = payment_method
    db.flush()
    return profile


def sync_payout_account(
    db: Session, user: User, *, release_funds: bool = False
) -> PayoutAccount | None:
    """Refresh payouts_enabled from the provider.

    Safe to call on a read: it only ever flips a boolean. Moving money is opt-in
    via `release_funds`, because this runs from a GET that clients and proxies
    retry freely — a read must never be the thing that triggers a payout.
    """
    account = db.get(PayoutAccount, user.id)
    if account is None:
        return None

    if not account.payouts_enabled:
        try:
            enabled = get_payment_provider().payouts_enabled(account.provider_account_ref)
        except ProviderError:
            return account  # provider hiccup must not break the screen
        if enabled:
            account.payouts_enabled = True
            db.flush()

    # Flush whenever funds may be released — not only in the moment the account
    # first turns on. A payout deferred by a transient provider failure (an
    # unsettled balance, an outage) leaves the payment CAPTURED with the money
    # owed on the ledger; if this only ran on the enable transition, that debt
    # would never be retried and would sit unpaid indefinitely.
    if account.payouts_enabled and release_funds:
        try:
            flush_pending_payouts(db, user.id)
        except ProviderError:
            # The account state is already correct, and the payout retries on
            # the next explicit attempt.
            logger.warning("deferred payout flush failed for user %s", user.id)
    return account


def create_payout_account(db: Session, user: User) -> tuple[PayoutAccount, str]:
    """Create the connected account, or hand back a fresh onboarding link for an
    existing one. Stripe's links are single-use and short-lived, so an account
    that exists but is not yet enabled still needs a new link every time."""
    if not user.can_work:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Workers only")
    account = sync_payout_account(db, user, release_funds=True)
    if account is not None:
        if account.payouts_enabled:
            return account, ""
        return account, get_payment_provider().onboarding_link(account.provider_account_ref)
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
    key = authorize_idempotency_key(job.id, price_cents, billing.default_payment_method_ref)
    try:
        auth_ref = execute_provider_call(
            db,
            key=key,
            operation="authorize",
            # No payment row exists yet — this call is what justifies creating
            # one — so the journal entry stands alone until then.
            payment_id=None,
            params={
                "amount": price_cents,
                "currency": job.currency,
                "customer": billing.provider_customer_ref,
                "payment_method": billing.default_payment_method_ref,
                "job_id": job.id,
            },
            fn=lambda: get_payment_provider().authorize(
                price_cents,
                job.currency,
                billing.provider_customer_ref,
                billing.default_payment_method_ref,
                metadata={"job_id": str(job.id)},
                idempotency_key=key,
            ),
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
    capture_key = capture_idempotency_key(payment)
    payment.provider_charge_ref = execute_provider_call(
        db,
        key=capture_key,
        operation="capture",
        payment_id=payment.id,
        params={"auth_ref": payment.provider_auth_ref},
        fn=lambda: get_payment_provider().capture(
            payment.provider_auth_ref, idempotency_key=capture_key
        ),
    )
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
    release_key = release_idempotency_key(payment)

    def _release() -> str:
        get_payment_provider().release(payment.provider_auth_ref, idempotency_key=release_key)
        # A cancellation creates no object of its own, so the journal records
        # the authorization it voided — the only reference there is.
        return payment.provider_auth_ref

    execute_provider_call(
        db,
        key=release_key,
        operation="release",
        payment_id=payment.id,
        params={"auth_ref": payment.provider_auth_ref},
        fn=_release,
    )
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
    # Keyed on the same identity as the ledger row below. The transfer happens
    # before any local write, so if a later step in this transaction fails the
    # database rolls back while the money has already moved; replaying the same
    # key returns the original transfer instead of sending a second one.
    payout_key = payout_idempotency_key(payment)
    try:
        payout_ref = execute_provider_call(
            db,
            key=payout_key,
            operation="transfer",
            payment_id=payment.id,
            params={
                "destination": account.provider_account_ref,
                "amount": payment.worker_net_cents,
                "currency": payment.currency,
            },
            fn=lambda: get_payment_provider().transfer(
                account.provider_account_ref,
                payment.worker_net_cents,
                payment.currency,
                metadata={"payment_id": str(payment.id)},
                idempotency_key=payout_key,
            ),
        )
    except ProviderError as exc:
        # A payout can fail for reasons unrelated to this job: an unsettled
        # platform balance, a provider outage, a restricted account. None of
        # that should undo the capture — the customer has paid, and the money is
        # already owed to the worker on the ledger. Leaving the payment CAPTURED
        # keeps that debt visible and lets flush_pending_payouts retry, instead
        # of rolling the completion back and stranding a real charge with no
        # local record of it.
        #
        # flush_pending_payouts also guards its own calls; the double guard is
        # deliberate, so neither caller depends on the other's handling.
        if exc.definitive:
            # Stripe rejected it outright, so nothing moved and its cache now
            # holds this error against the current key. Advance the counter so
            # the next attempt uses a fresh one; reusing it would replay this
            # failure until the cache expires and the worker would go unpaid.
            payment.payout_attempts += 1
        logger.warning(
            "payout deferred for payment %s (attempt %s): %s",
            payment.id, payment.payout_attempts, exc,
        )
        return
    # Flush before opening the savepoint so that nothing pending from an earlier
    # payment in flush_pending_payouts' loop is inside its scope. Otherwise the
    # autoflush that post_transaction triggers would carry a previous payment's
    # PAID_OUT into this savepoint, and the rollback below would silently revert
    # a payout that already succeeded at the provider.
    db.flush()
    try:
        # Concurrent flushes (the onboarding webhook racing GET/POST
        # /me/payout-account) can both pass the ledger's exists-check before
        # either commits; the loser then trips uq_ledger_txn_account. The
        # transfer above was already deduplicated by its idempotency key, so
        # the collision only means the rows are recorded — roll back this
        # savepoint and carry on as the no-op it is.
        with db.begin_nested():
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
    except IntegrityError:
        logger.info(
            "payout ledger for payment %s already recorded by a concurrent flush", payment.id
        )
    payment.provider_payout_ref = payout_ref
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
        try:
            _try_payout(db, payment)
        except ProviderError:
            # One worker's failed transfer must not abort the others, and must
            # not roll back payouts that already succeeded in this loop. The
            # payment stays CAPTURED and is retried on the next flush.
            logger.warning("payout failed for payment %s", payment.id, exc_info=True)
            continue
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
    # Worker and platform give back proportional shares; rounding lands on platform.
    worker_share = amount * payment.worker_net_cents // payment.amount_cents
    platform_share = amount - worker_share
    reverse_payout = payment.status == PaymentStatus.PAID_OUT and worker_share > 0
    # The customer's refund goes first, and the worker's claw-back second.
    #
    # The reverse order is the tempting one — claw back before giving anything
    # away — and it is wrong, because a reversal is irreversible at the provider
    # while this transaction is not yet durable. If the refund then fails, the
    # reversal is orphaned: the database rolls back knowing nothing about it, and
    # an admin retrying at a *different* amount mints a second reversal under a
    # different key. Measured: a failed 5000 refund left 4250 clawed back, and a
    # 3000 retry took another 2550 — 6800 collected from a worker for a 3000
    # refund. Refunding first cannot orphan anything the retry path double-counts.
    #
    # The refund goes out through the journal, which is what makes the retry
    # after a rolled-back transaction safe: a generation whose refund already
    # succeeded is replayed from the record instead of being sent again, and a
    # retry at a different amount is refused rather than stacked on top of it.
    refund_key = refund_idempotency_key(payment)
    try:
        execute_provider_call(
            db,
            key=refund_key,
            operation="refund",
            payment_id=payment.id,
            params={"charge": payment.provider_charge_ref, "amount": amount},
            fn=lambda: get_payment_provider().refund(
                payment.provider_charge_ref, amount, idempotency_key=refund_key
            ),
        )
    except ProviderCallConflict as exc:
        # An admin retrying the same refund at a new amount. Answer them
        # directly — this is an admin-only route and the detail is the whole
        # value: a generic 502 would send them to the logs to find out that a
        # refund may already be sitting at the provider.
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    # Zero-value entries are rejected by the ledger, and a refund small enough
    # that the worker's proportional share floors to zero is a legitimate refund,
    # not a corrupt transaction. (A 1-cent refund raised UnbalancedTransaction.)
    entries = [("external:card_network", amount)]
    if worker_share:
        entries.append((f"worker:{payment.worker_id}", -worker_share))
    if platform_share:
        entries.append(("platform:revenue", -platform_share))
    ledger.post_transaction(
        db,
        txn_key=f"refund:{payment.id}:{payment.refunded_cents + amount}",
        currency=payment.currency,
        entries=entries,
        payment_id=payment.id,
    )
    if reverse_payout:
        # The worker's share already left for their connected account, so claw it
        # back — otherwise their ledger stays negative and the platform eats the
        # loss. Keyed on the cumulative refunded amount so a replay is a no-op.
        reversal_key = reversal_idempotency_key(payment, amount)
        try:
            execute_provider_call(
                db,
                key=reversal_key,
                operation="reverse_transfer",
                payment_id=payment.id,
                params={"transfer": payment.provider_payout_ref, "amount": worker_share},
                fn=lambda: get_payment_provider().reverse_transfer(
                    payment.provider_payout_ref,
                    worker_share,
                    idempotency_key=reversal_key,
                ),
            )
        except ProviderError:
            # Deliberately not fatal. The customer has been refunded and that
            # must stand; failing here would roll the refund out of the database
            # while it stayed done at the provider, and the retry would refund
            # them a second time. Leaving the worker un-clawed is the documented
            # pre-existing behaviour — the platform absorbs it — and it is
            # visible as a negative worker balance rather than silent.
            logger.warning(
                "transfer reversal failed for payment %s; worker:%s keeps %s cents "
                "and the platform absorbs it. Ledger balance will show the shortfall.",
                payment.id, payment.worker_id, worker_share, exc_info=True,
            )
        else:
            # The refund entries above drove the worker's balance negative by their
            # share; the reversal brings that money back off the payout rail and
            # squares them, keeping every account at zero net.
            ledger.post_transaction(
                db,
                txn_key=f"reverse:{payment.id}:{payment.refunded_cents + amount}",
                currency=payment.currency,
                entries=[
                    ("external:payouts", -worker_share),
                    (f"worker:{payment.worker_id}", worker_share),
                ],
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
