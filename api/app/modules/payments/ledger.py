"""The only write path into the double-entry ledger."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.payments.models import LedgerEntry


class UnbalancedTransaction(Exception):
    pass


def post_transaction(
    db: Session,
    txn_key: str,
    currency: str,
    entries: list[tuple[str, int]],
    payment_id: int | None = None,
) -> bool:
    """Write a balanced set of ledger entries. Idempotent on txn_key: returns False
    (writing nothing) if the transaction was already posted."""
    total = sum(amount for _, amount in entries)
    if total != 0:
        raise UnbalancedTransaction(f"Transaction '{txn_key}' sums to {total}, not 0")
    if any(amount == 0 for _, amount in entries):
        raise UnbalancedTransaction(f"Transaction '{txn_key}' contains a zero entry")

    exists = db.scalar(select(LedgerEntry.id).where(LedgerEntry.txn_key == txn_key).limit(1))
    if exists is not None:
        return False

    for account, amount in entries:
        db.add(
            LedgerEntry(
                txn_key=txn_key,
                account=account,
                amount_cents=amount,
                currency=currency,
                payment_id=payment_id,
            )
        )
    db.flush()
    return True


def account_balance(db: Session, account: str) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)).where(
            LedgerEntry.account == account
        )
    )


def trial_balance(db: Session) -> int:
    """Sum of every entry in the ledger. Anything other than 0 means corruption."""
    return db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_cents), 0)))
