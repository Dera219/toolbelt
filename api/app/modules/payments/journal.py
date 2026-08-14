"""The one road to the money rails.

Every provider call that moves money goes through `execute_provider_call`, which
writes a durable record of the attempt *before* the call and the outcome after
it. That ordering is the whole point.

The hazard it closes
--------------------
A provider call is an irreversible external effect executed from inside a
database transaction that is not yet durable. Capture, transfer, refund and
reversal all run before their local rows are written, so any failure later in
the same transaction rolls the database back while the money has already moved.
The retry then starts from a database that has no memory of the first attempt,
and it pays a second time. Every payment defect this codebase has found —
the double payout, the over-clawed reversal, the twice-issued refund — is that
one hazard wearing a different hat.

Provider idempotency keys alone do not close it. They are the necessary half:
replaying a key returns the original object instead of moving money again. But
Stripe prunes keys once they are 24 hours old and then treats a reuse as a
brand-new request ("We generate a new request if a key is reused after the
original is pruned"), so a repair attempted the next morning is a second real
refund. The journal is the durable half: it remembers the attempt for as long as
the row exists, and it answers a replay without going to the provider at all.

What it guarantees
------------------
- A key recorded `succeeded` is never sent again. The stored provider ref is
  returned and the caller's transaction can be re-run to completion.
- A key that comes back with a different fingerprint is refused *before* the
  call, loudly, naming the payment. That is the "retry at a different amount"
  case: the first attempt may already have moved money, so a second call at new
  terms could pay twice.
- `pending` and `failed` both mean "outcome unknown or replayable", so the call
  is re-issued under the *same* key and the provider deduplicates it. Minting a
  fresh key on failure is what the payout counter does deliberately and
  elsewhere; it must never happen by accident here.
- If the journal cannot be written, no money moves. Fail closed.

What it does not guarantee
--------------------------
It cannot make the provider call and the local record atomic — nothing can; the
call crosses a network. A crash between the call and the completion write leaves
the row `pending`, which is exactly right: the outcome genuinely is unknown, and
the retry re-sends under the same key. The journal converts a silent double
spend into a named, visible inconsistency. That is the achievable guarantee.

Durability on SQLite
--------------------
A record that rolls back with the transaction it is meant to outlive is worse
than no record, because it looks like a guarantee. So the row is committed on a
second connection. Postgres — what production runs — grants that always.

SQLite does not: it serializes writes at the file level, so a second connection
cannot commit while the business transaction holds the write lock. Measured, on
this repo's own configuration: with the business transaction holding only reads
the autonomous commit succeeds in 1 ms; once it has written a row, the second
connection fails with SQLITE_BUSY. The journal therefore uses a dedicated SQLite
engine with a zero busy-timeout so the refusal is instant rather than a five
second stall, and falls back to the business session with a warning when it is
refused. On that path the record is NOT durable, and the protection degrades to
the provider's own idempotency key.

That degradation is narrower than it sounds. The refund and dispute paths read
before they call, so their journal rows commit autonomously even on SQLite; it
is the authorize inside `accept_offer` — which has already written the offer and
job rows — that degrades. Production is Postgres, where none of this applies.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.db import SessionLocal, engine
from app.modules.identity.models import utcnow
from app.modules.payments.models import ProviderCall, ProviderCallStatus
from app.modules.payments.provider import ProviderError

logger = logging.getLogger(__name__)

# Every money-moving operation. Named here so the set is greppable and so a
# sixth one has an obvious place to announce itself.
OPERATIONS = ("authorize", "capture", "release", "refund", "transfer", "reverse_transfer")


class ProviderCallConflict(ProviderError):
    """The same idempotency key was already used with different parameters.

    `definitive` stays False on purpose. It reads as "nothing moved, mint a
    fresh key and retry", and here the opposite is true: we refused precisely
    because an earlier call under this key may already have moved money. A fresh
    key would send the second one.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, definitive=False)


class JournalUnavailable(ProviderError):
    """The attempt could not be recorded, so it was never made.

    `definitive` is True: no call went out, so a later retry under a fresh key
    is safe.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, definitive=True)


# --------------------------------------------------------------- the second connection

_autonomous_sessions: sessionmaker | None = None


def _autonomous_sessionmaker() -> sessionmaker:
    """Sessions on a connection of their own, so a commit here survives a
    rollback there.

    On anything but SQLite the application pool already hands out independent
    connections, so it is reused. SQLite gets a dedicated engine: NullPool so no
    connection is ever held between calls (a pooled reader is what makes
    `drop_all` a silent no-op in the test suite), and `timeout=0` so a write
    lock held by the business transaction is refused immediately instead of
    after the five second default.
    """
    global _autonomous_sessions
    if _autonomous_sessions is None:
        if engine.dialect.name == "sqlite":
            _autonomous_sessions = sessionmaker(
                bind=create_engine(
                    engine.url,
                    connect_args={"check_same_thread": False, "timeout": 0},
                    poolclass=NullPool,
                ),
                autoflush=False,
                expire_on_commit=False,
            )
        else:
            _autonomous_sessions = SessionLocal
    return _autonomous_sessions


def _is_write_lock(exc: OperationalError) -> bool:
    """SQLite refusing a second concurrent writer, as opposed to a real fault."""
    name = getattr(getattr(exc, "orig", None), "sqlite_errorname", "")
    if name in ("SQLITE_BUSY", "SQLITE_LOCKED"):
        return True
    return "database is locked" in str(exc) or "database table is locked" in str(exc)


def _run_autonomously(work: Callable[[Session], None]) -> bool:
    """Run `work` on its own connection and commit it.

    True when it committed. False only when the backend refused a second
    concurrent writer — the caller then decides what to do without durability.
    Every other failure propagates, because a journal write that failed for a
    real reason must stop the money.
    """
    session = _autonomous_sessionmaker()()
    try:
        work(session)
        session.commit()
        return True
    except OperationalError as exc:
        session.rollback()
        if not _is_write_lock(exc):
            raise
        return False
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------- journal reads and writes


def _fingerprint(params: dict[str, Any]) -> str:
    """Stable hash of a call's parameters.

    Canonical JSON — sorted keys, no incidental whitespace — so that the same
    call fingerprints identically across processes and Python versions.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _lookup(db: Session, key: str):
    """The recorded attempt for this key, or None.

    Read through the *business* session so it sees both committed rows and a row
    this transaction wrote itself on the degraded path. Selecting columns rather
    than the entity keeps the ORM identity map out of it: the autonomous session
    updates the same row behind this session's back, and a cached entity would
    answer with a stale status.
    """
    return db.execute(
        select(
            ProviderCall.status,
            ProviderCall.request_fingerprint,
            ProviderCall.provider_ref,
        ).where(ProviderCall.idempotency_key == key)
    ).first()


def _record(
    db: Session, *, key: str, operation: str, payment_id: int | None, fingerprint: str
) -> None:
    """Write the `pending` row that has to exist before the provider is called."""

    def row() -> ProviderCall:
        return ProviderCall(
            idempotency_key=key,
            operation=operation,
            payment_id=payment_id,
            request_fingerprint=fingerprint,
            status=ProviderCallStatus.PENDING,
        )

    if _run_autonomously(lambda session: session.add(row())):
        return
    # SQLite, business transaction holding the write lock. Riding it is the
    # strongest thing left: the row is real for the rest of this transaction and
    # still catches an in-transaction replay, but it dies with a rollback, so
    # the protection falls back to the provider's own idempotency key.
    logger.warning(
        "provider-call journal is not durable for %s (key %s): the backend refused a "
        "second concurrent writer, so this record rolls back with the business "
        "transaction and only the provider's idempotency key protects the retry",
        operation, key,
    )
    # Flush first so that nothing already pending on this session ends up inside
    # the savepoint below — the same reasoning `_try_payout` applies before its
    # ledger savepoint. Then a duplicate key surfaces as a catchable
    # IntegrityError instead of poisoning the business transaction around it.
    db.flush()
    with db.begin_nested():
        db.add(row())
        db.flush()


def _finish(
    db: Session, *, key: str, status: ProviderCallStatus, provider_ref: str | None, error: str | None
) -> None:
    """Close out a recorded attempt. Never raises.

    By the time this runs the provider call has already happened. Raising here
    would roll the business transaction back over money that already moved —
    the exact defect this module exists to prevent. A completion that cannot be
    written leaves the row `pending`, which the retry path reads as "unknown
    outcome, re-send under the same key". Correct, just less informative.
    """
    stmt = (
        update(ProviderCall)
        .where(ProviderCall.idempotency_key == key)
        .values(
            status=status,
            provider_ref=provider_ref,
            error=error,
            completed_at=utcnow(),
        )
    )
    updated = 0

    def work(session: Session) -> None:
        nonlocal updated
        updated = session.execute(stmt).rowcount

    try:
        if _run_autonomously(work) and updated:
            return
    except SQLAlchemyError:
        logger.warning("could not close journal entry %s autonomously", key, exc_info=True)
    # Either the backend refused a second writer, or the pending row was written
    # on the degraded path and is only visible inside this transaction. Both
    # end in the same place.
    try:
        db.execute(stmt)
    except SQLAlchemyError:
        logger.error(
            "journal entry %s stays 'pending' after a %s call — the outcome is "
            "recorded only in the provider. Reconcile before retrying.",
            key, status.value, exc_info=True,
        )


# --------------------------------------------------------------- the one road


def execute_provider_call(
    db: Session,
    *,
    key: str,
    operation: str,
    payment_id: int | None,
    params: dict[str, Any],
    fn: Callable[[], str],
) -> str:
    """Record the intent, then make the call, then record the outcome.

    `key` is the provider idempotency key and the journal's natural key at once;
    they are deliberately the same string so that the local record and the
    provider's deduplication can never disagree about what "the same call"
    means. `params` is what the provider is being asked for, and is what a
    retry is checked against.

    Returns the provider's reference for the call — replayed from the journal
    when this key has already succeeded, in which case the provider is not
    contacted at all.
    """
    if operation not in OPERATIONS:
        raise ValueError(f"unknown money operation {operation!r}")
    fingerprint = _fingerprint(params)

    recorded = _lookup(db, key)
    if recorded is None:
        try:
            _record(
                db, key=key, operation=operation, payment_id=payment_id, fingerprint=fingerprint
            )
        except IntegrityError:
            # Another process recorded this key between the read and the write.
            # Its row is authoritative; fall through and honour it.
            recorded = _lookup(db, key)
            if recorded is None:
                raise JournalUnavailable(
                    f"could not record the {operation} attempt for payment {payment_id}; "
                    "no call was made"
                ) from None
        except SQLAlchemyError as exc:
            raise JournalUnavailable(
                f"could not record the {operation} attempt for payment {payment_id}: {exc}. "
                "No call was made."
            ) from exc

    if recorded is not None:
        if recorded.request_fingerprint != fingerprint:
            raise ProviderCallConflict(
                f"A {operation} for payment {payment_id} was already attempted under "
                f"idempotency key '{key}' with different parameters, and is recorded as "
                f"'{recorded.status.value}'. Money may already have moved for that attempt, "
                "so this one is refused rather than risk a second one. Reconcile this "
                "payment against the provider; retrying at the original amount replays the "
                "first attempt safely."
            )
        if recorded.status == ProviderCallStatus.SUCCEEDED:
            # Already done at the provider. Returning the stored reference lets
            # the caller's transaction be re-run to completion without paying
            # twice — including long after the provider has pruned the key.
            logger.info("replaying journalled %s %s from key %s", operation, recorded.provider_ref, key)
            return recorded.provider_ref
        # pending or failed: the outcome is unknown or the failure is
        # replayable. Re-send under the SAME key and let the provider decide.

    try:
        provider_ref = fn()
    except Exception as exc:
        # `failed` records the last error; it does not assert that nothing
        # moved. That is why the retry path treats it exactly like `pending`.
        _finish(db, key=key, status=ProviderCallStatus.FAILED, provider_ref=None, error=str(exc)[:2000])
        raise
    _finish(
        db, key=key, status=ProviderCallStatus.SUCCEEDED, provider_ref=provider_ref, error=None
    )
    return provider_ref
