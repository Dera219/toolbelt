"""The provider-call journal: replay, refusal, durability, and failing closed.

These test the mechanism directly. The money flows that depend on it are proved
end to end in test_payments.py and test_payout_sync.py; the point here is to pin
down the four behaviours everything else assumes.
"""

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal, engine
from app.modules.payments import journal
from app.modules.payments.journal import (
    JournalUnavailable,
    ProviderCallConflict,
    execute_provider_call,
)
from app.modules.payments.models import ProviderCall, ProviderCallStatus

PARAMS = {"charge": "ch_test", "amount": 5000}


class Provider:
    """A stand-in that counts how many times it was actually reached."""

    def __init__(self, ref: str = "re_first", fail: bool = False) -> None:
        self.calls = 0
        self.ref = ref
        self.fail = fail

    def __call__(self) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider is down")
        return self.ref


@pytest.fixture()
def db(clean_db):
    with SessionLocal() as session:
        yield session


def _entry(key: str) -> ProviderCall | None:
    """Read the journal from a connection of its own, so what comes back is what
    is actually committed rather than what some session is holding."""
    with SessionLocal() as fresh:
        return fresh.scalar(select(ProviderCall).where(ProviderCall.idempotency_key == key))


def test_a_succeeded_key_replays_without_calling_the_provider(db):
    provider = Provider("re_original")
    first = execute_provider_call(
        db, key="refund-call:1:0", operation="refund", payment_id=1, params=PARAMS, fn=provider
    )
    replay = execute_provider_call(
        db, key="refund-call:1:0", operation="refund", payment_id=1, params=PARAMS, fn=provider
    )

    assert first == replay == "re_original"
    assert provider.calls == 1, "a recorded success must never be sent again"
    assert _entry("refund-call:1:0").status is ProviderCallStatus.SUCCEEDED


def test_the_same_key_with_different_parameters_is_refused_before_the_call(db):
    provider = Provider()
    execute_provider_call(
        db, key="refund-call:2:0", operation="refund", payment_id=2, params=PARAMS, fn=provider
    )

    with pytest.raises(ProviderCallConflict) as raised:
        execute_provider_call(
            db,
            key="refund-call:2:0",
            operation="refund",
            payment_id=2,
            params={**PARAMS, "amount": 3000},
            fn=provider,
        )

    assert provider.calls == 1, "the second call must not reach the provider at all"
    message = str(raised.value)
    assert "payment 2" in message and "reconcile" in message.lower()
    # Not definitive: an earlier call under this key may have moved money, so a
    # caller must not read this as "nothing happened, mint a fresh key".
    assert raised.value.definitive is False


def test_a_pending_entry_is_re_sent_under_the_same_key(db):
    """The crash between the call and the commit.

    The process dies after the provider was contacted and before the outcome
    was recorded, leaving a `pending` row. The outcome genuinely is unknown, so
    the only safe move is to send it again under the *same* key and let the
    provider deduplicate. Minting a fresh key here is what pays twice.
    """
    provider = Provider("re_crashed", fail=True)
    with pytest.raises(RuntimeError):
        execute_provider_call(
            db, key="refund-call:3:0", operation="refund", payment_id=3, params=PARAMS, fn=provider
        )
    entry = _entry("refund-call:3:0")
    assert entry.status is ProviderCallStatus.FAILED
    assert "provider is down" in entry.error

    recovered = Provider("re_recovered")
    assert (
        execute_provider_call(
            db, key="refund-call:3:0", operation="refund", payment_id=3, params=PARAMS,
            fn=recovered,
        )
        == "re_recovered"
    )
    assert recovered.calls == 1
    assert _entry("refund-call:3:0").status is ProviderCallStatus.SUCCEEDED


def test_a_journal_that_cannot_be_written_stops_the_money(db, monkeypatch):
    """Fail closed. An unrecordable call is a call that must not happen."""
    from sqlalchemy.exc import SQLAlchemyError

    def broken(*args, **kwargs):
        raise SQLAlchemyError("journal database is unreachable")

    monkeypatch.setattr(journal, "_record", broken)
    provider = Provider()
    with pytest.raises(JournalUnavailable) as raised:
        execute_provider_call(
            db, key="refund-call:4:0", operation="refund", payment_id=4, params=PARAMS, fn=provider
        )
    assert provider.calls == 0, "money must not move on a journal we could not write"
    # Nothing went out, so a later retry under a fresh key is safe.
    assert raised.value.definitive is True


def test_an_unknown_operation_is_rejected(db):
    """The operation list is the register of things that move money. A call that
    is not on it is a mistake, not a new feature."""
    provider = Provider()
    with pytest.raises(ValueError):
        execute_provider_call(
            db, key="x:1", operation="wire_transfer", payment_id=1, params=PARAMS, fn=provider
        )
    assert provider.calls == 0


def test_the_record_outlives_the_business_transaction(db):
    """The property the whole design rests on.

    The refund path reads before it calls, so the journal gets its own
    connection and commits there. When the surrounding transaction then rolls
    back — which is the defect being defended against — the record stays, and
    the retry replays instead of paying again.
    """
    provider = Provider("re_durable")
    execute_provider_call(
        db, key="refund-call:5:0", operation="refund", payment_id=5, params=PARAMS, fn=provider
    )
    db.rollback()

    entry = _entry("refund-call:5:0")
    assert entry is not None, "the record must survive the rollback it exists to outlive"
    assert entry.status is ProviderCallStatus.SUCCEEDED
    assert entry.provider_ref == "re_durable"

    with SessionLocal() as after_rollback:
        assert (
            execute_provider_call(
                after_rollback, key="refund-call:5:0", operation="refund", payment_id=5,
                params=PARAMS, fn=provider,
            )
            == "re_durable"
        )
    assert provider.calls == 1


@pytest.mark.skipif(
    engine.dialect.name != "sqlite",
    reason="only SQLite refuses a second concurrent writer",
)
def test_the_record_degrades_when_the_backend_refuses_a_second_writer(db, caplog):
    """The documented limit, asserted rather than assumed.

    SQLite serializes writes at the file level, so a business transaction that
    has already written blocks the journal's own connection. The row then rides
    that transaction and dies with it, and the protection falls back to the
    provider's idempotency key. This is why production runs Postgres — and why
    the fallback is logged rather than silent.
    """
    from app.modules.payments.models import WebhookEvent

    # Any write at all is enough to take the file's write lock.
    db.add(WebhookEvent(id="evt_takes_the_write_lock", event_type="test"))
    db.flush()

    provider = Provider("re_degraded")
    with caplog.at_level("WARNING"):
        execute_provider_call(
            db, key="refund-call:6:0", operation="refund", payment_id=6, params=PARAMS,
            fn=provider,
        )
    assert any("not durable" in record.getMessage() for record in caplog.records)

    db.rollback()
    assert _entry("refund-call:6:0") is None, (
        "the degraded record is expected to die with its transaction — if this "
        "ever passes, the fallback path became durable and the warning is a lie"
    )
