"""Ask the provider what actually happened to a call we never got an answer for.

The journal (journal.py) writes a `pending` row, makes the provider call, then
writes the outcome. A crash in between leaves the row `pending` forever, and its
own docstring is honest about it: "the outcome genuinely is unknown". Nothing in
this codebase ever asked the provider, so `pending` meant unknown *permanently*.
This module is what asks.

What it is for
--------------
Distinguishing **"nothing happened"** from **"I cannot tell"**. Those two are the
same word — `pending` — in the journal, and they are completely different
operationally: the first is a job that silently did not get paid, the second is
money that may be sitting somewhere nobody is looking. A reconciler that
collapses them into one answer is not a reconciler, it is a liar with a schedule.

Every resolution therefore lands in one of three buckets, and UNKNOWN is a
first-class result rather than a failure of the tool.

The policy: knowledge gap, never money gap
------------------------------------------
This module may move a journal row from `pending` to `succeeded` or `failed` and
record the true provider reference. It may not do anything else. It never issues
a refund, transfer, capture or reversal; it never edits a Payment row; it never
writes a ledger entry. When the journal's truth and the local business state
disagree — the customer was charged and no payment says so, the worker was paid
twice — that is reported as a `discrepancy` for a human, carrying the payment id,
the provider reference, the amount, and the ledger transaction key that is
missing.

The reason is not squeamishness. A sweeper that repairs money state is a second
money-moving code path with no idempotency key, no journal row and no human in
the loop, running unattended against the exact rows we already know we are
confused about. That is a new class of bug wearing the costume of a fix.

The prohibition is enforced by construction rather than by review: the provider
is wrapped in `_ReadOnlyProvider` before this module can touch it, and any
money-moving method raises `MoneyMovementRefused`.

Why it never replays a call
---------------------------
The tempting shortcut is to re-issue the original request under its original
idempotency key: inside Stripe's 24-hour key window a replay returns the original
object and moves nothing. That reasoning has a hole exactly the size of this
module's purpose. A replay returns the original object *only if the key was ever
consumed* — and "was the key consumed?" is precisely the question being asked. A
row is `pending` because we do not know whether the request reached the provider
at all. If it did not, the replay does not observe the past, it creates a brand
new charge: the sweeper would have caused the money movement it was sent to
investigate, in exactly the case ("nothing happened") it exists to detect.

So there is one code path, in both windows, and it is read-only. That is strictly
safer than a two-window design and it is less code.

Which direction is dangerous
----------------------------
Not symmetric, and the design leans on the asymmetry. `pending` and `failed` are
already treated identically by the retry path — both mean "unknown or
replayable", both re-send under the same key — so relabelling `pending` as
`failed` arms nothing; it only tells a human something truer. Writing
`succeeded` is the dangerous direction: from then on the journal replays the
stored reference *without contacting the provider at all*. A wrong reference
there is a capture that never gets made or a refund silently considered done.
Every SUCCEEDED verdict below therefore requires a positively identified
provider object, never an inference from absence.

How a call is attributed to a provider object
---------------------------------------------
For capture and release the object is already known — the Payment's
authorization reference — so the answer is a state read.

For authorize, refund, transfer and reversal, the provider object was created by
the very call whose reference we lost, so it has to be identified by its
contents. The journal already holds the perfect witness: `request_fingerprint`,
the hash of the parameters we asked for, written *before* the call. Candidate
objects are pulled from the provider, the call parameters are reconstructed from
each candidate, and the one whose fingerprint equals the stored fingerprint is
ours. Candidates already claimed as another journal row's `provider_ref` are
excluded first, because two refunds of the same amount on the same charge
fingerprint identically and only an unclaimed one can belong to this row.

Exactly one match resolves it. Zero matches means the call never landed. More
than one means duplicate objects exist at the provider — which is the
double-spend itself, and is reported rather than resolved.

The grace period
----------------
A row written thirty seconds ago is a call that is very likely still in flight.
Judging it races the live attempt, and losing that race means recording "nothing
moved" over money that lands a second later. Rows are left alone until they are
`older_than_minutes` old (default 15).
"""

import enum
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.identity.models import utcnow
from app.modules.jobs.models import Job

# `_fingerprint` is imported rather than reimplemented on purpose. The hash this
# module computes has to be bit-identical to the one the journal wrote before the
# call; a private copy that drifted by a key name or a separator would not fail,
# it would silently stop matching and turn every resolvable row into "unknown".
from app.modules.payments.journal import OPERATIONS, _fingerprint
from app.modules.payments.models import (
    BillingProfile,
    LedgerEntry,
    Payment,
    PaymentStatus,
    PayoutAccount,
    ProviderCall,
    ProviderCallStatus,
)
from app.modules.payments.provider import (
    AuthorizationRecord,
    AuthorizationState,
    ProviderError,
    get_payment_provider,
)

logger = logging.getLogger(__name__)

# Rows younger than this are still plausibly in flight. See "The grace period".
DEFAULT_GRACE_MINUTES = 15
# One sweep's worth of rows. A run that finds thousands of pending calls has a
# bigger problem than throughput, and an unbounded scan against production is how
# a reconciler becomes an outage.
DEFAULT_LIMIT = 200
# Slack applied when asking the provider for objects created "since" a journal
# row was written. Covers clock skew between this host and the provider; without
# it a provider clock a few seconds ahead of ours hides the very object we are
# looking for, and the sweeper reports "nothing moved" over a real charge.
LOOKBACK_SLACK = timedelta(minutes=5)

# `authorize:{job_id}:{amount_cents}:{payment_method_ref[-14:]}` — the only key
# whose row carries no payment_id, because the authorize is what justifies
# creating the payment. Everything its lookup needs is therefore in the key.
_AUTHORIZE_KEY = re.compile(r"^authorize:(?P<job_id>\d+):(?P<amount>\d+):(?P<pm_tail>.*)$")
# `refund-call:{payment_id}:{refunded_cents_before}`. The generation is not what
# identifies the refund — the fingerprint does that — but it is what names the
# ledger transaction that should exist for it.
_REFUND_KEY = re.compile(r"^refund-call:(?P<payment_id>\d+):(?P<generation>\d+)$")
# `reverse:{payment_id}:{refunded_cents_after}`. Same role: the claw-back's
# ledger transaction key.
_REVERSAL_KEY = re.compile(r"^reverse:(?P<payment_id>\d+):(?P<cumulative>\d+)$")


class MoneyMovementRefused(Exception):
    """The sweeper tried to call a money-moving provider method.

    Raised by the read-only guard and never caught. Seeing this in a log means
    reconciliation grew a mutation, which is the one thing it must not have — so
    it fails loudly instead of moving the money and explaining afterwards.
    """


# The complete set of provider methods reconciliation may call. An allowlist
# rather than a blocklist on purpose: a provider method added later is forbidden
# by default, which is the correct direction for a component whose entire safety
# argument is "it cannot move money".
_READ_ONLY_METHODS = frozenset(
    {
        "lookup_authorization",
        "lookup_authorizations_for_customer",
        "lookup_refunds",
        "lookup_transfers_to",
        "lookup_transfer_reversals",
    }
)


class _ReadOnlyProvider:
    """A provider that can only be read from.

    The structural half of the money-movement prohibition. The policy is stated
    in the module docstring, but a policy in a docstring is one a future edit can
    violate in silence; this makes the violation raise.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name not in _READ_ONLY_METHODS:
            raise MoneyMovementRefused(
                f"reconciliation attempted to call provider.{name}(). The sweeper closes "
                "the knowledge gap, never the money gap: it may relabel a journal row and "
                "it may not move a cent. A repair that is genuinely needed goes through "
                "the normal money path, so it gets an idempotency key and a journal row "
                "of its own."
            )
        return getattr(self._inner, name)


# ---------------------------------------------------------------- results


class Resolution(str, enum.Enum):
    SUCCEEDED = "succeeded"  # the call landed; the journal now holds the real ref
    FAILED = "failed"  # positive evidence that nothing landed
    UNKNOWN = "unknown"  # cannot tell; left pending and reported


@dataclass
class Outcome:
    key: str
    operation: str
    payment_id: int | None
    resolution: Resolution
    provider_ref: str | None
    # Always populated, for every resolution. The evidence, in a sentence a
    # human can act on without reading this file.
    detail: str
    # Set when the journal's truth and the local business state disagree. Never
    # repaired here — this is the handoff to a person.
    discrepancy: str | None = None
    # False in dry-run, and also when a concurrent live retry resolved the row
    # first and the conditional update matched nothing.
    written: bool = False


@dataclass
class ReconciliationReport:
    dry_run: bool
    grace_minutes: int
    # Pending rows that exist but are too young to judge. Reported rather than
    # hidden: "nothing to do" and "three calls are in flight right now" are
    # different states of the world.
    in_grace_period: int
    outcomes: list[Outcome]

    @property
    def scanned(self) -> int:
        return len(self.outcomes)

    def count(self, resolution: Resolution) -> int:
        return sum(1 for o in self.outcomes if o.resolution is resolution)

    @property
    def discrepancies(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.discrepancy]

    @property
    def unresolved(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.resolution is Resolution.UNKNOWN]


@dataclass
class _Verdict:
    resolution: Resolution
    provider_ref: str | None = None
    detail: str = ""
    discrepancy: str | None = None


def _unknown(detail: str, discrepancy: str | None = None) -> _Verdict:
    return _Verdict(Resolution.UNKNOWN, None, detail, discrepancy)


def _failed(detail: str, discrepancy: str | None = None) -> _Verdict:
    return _Verdict(Resolution.FAILED, None, detail, discrepancy)


def _succeeded(ref: str, detail: str, discrepancy: str | None = None) -> _Verdict:
    return _Verdict(Resolution.SUCCEEDED, ref, detail, discrepancy)


@dataclass
class _Context:
    db: Session
    provider: Any
    # Provider references already spoken for by some journal row. Two calls with
    # identical parameters fingerprint identically — a 2000-cent refund of
    # generation 0 and another of generation 2000 on the same charge are
    # indistinguishable by content — so the only thing separating them is that
    # one object is already claimed.
    claimed: set[str]


# ---------------------------------------------------------------- shared helpers


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes for `DateTime(timezone=True)` columns;
    Postgres hands back aware ones. Comparing the two raises, so normalize. The
    stored value is UTC either way — `utcnow()` is the only writer."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _ledger_has(db: Session, txn_key: str) -> bool:
    return (
        db.scalar(select(LedgerEntry.id).where(LedgerEntry.txn_key == txn_key).limit(1))
        is not None
    )


def _money(cents: int, currency: str) -> str:
    return f"{cents} minor units of {currency.upper()}"


def _matching(
    ctx: _Context, row: ProviderCall, candidates: list, params_of: Callable[[Any], dict]
) -> list:
    """Candidates that are unclaimed and whose reconstructed parameters hash to
    the fingerprint recorded before the call.

    The fingerprint is the strongest evidence available and it costs nothing: it
    was written *before* the provider was contacted, so it is an independent
    witness to what we asked for, and it survives even though the parameters
    themselves were never stored.
    """
    return [
        candidate
        for candidate in candidates
        if candidate.ref not in ctx.claimed
        and _fingerprint(params_of(candidate)) == row.request_fingerprint
    ]


# ---------------------------------------------------------------- per-operation resolvers


def _resolve_authorize(ctx: _Context, row: ProviderCall) -> _Verdict:
    """The hardest one, and the only operation with a genuinely unresolvable case.

    An authorize crashes before any Payment row exists, so there is no stored
    reference to retrieve — by construction, since the authorize is what
    justifies creating the payment. The key is the only surviving description of
    the call, and it carries the job and the amount. Everything else has to be
    rebuilt: job → customer → billing profile → provider customer, and then the
    provider's own list of that customer's recent authorizations.

    When that chain breaks — an unparseable key, a job that no longer exists, a
    customer with no billing profile — the sweeper has no handle on the provider
    side at all and says so. It does not guess.
    """
    parsed = _AUTHORIZE_KEY.match(row.idempotency_key)
    if parsed is None:
        return _unknown(
            f"idempotency key '{row.idempotency_key}' is not in the authorize format "
            "'authorize:{job_id}:{amount}:{pm_tail}', so the job and amount this call "
            "was for cannot be recovered. Nothing else identifies it."
        )
    job_id = int(parsed.group("job_id"))
    amount_cents = int(parsed.group("amount"))
    pm_tail = parsed.group("pm_tail")

    job = ctx.db.get(Job, job_id)
    if job is None:
        return _unknown(
            f"job {job_id} no longer exists, so the customer this authorization was "
            "placed against cannot be identified and the provider cannot be asked."
        )
    billing = ctx.db.get(BillingProfile, job.customer_id)
    if billing is None:
        return _unknown(
            f"customer {job.customer_id} (job {job_id}) has no billing profile, so there "
            "is no provider customer to search for this authorization under. This call "
            "cannot be resolved by any means available here."
        )

    def params_of(candidate: AuthorizationRecord) -> dict:
        # Rebuilt exactly as authorize_for_job built it. `currency` comes from
        # the job rather than the provider object because the local value is
        # what was hashed ("USD"), while the provider stores it lowercased. The
        # payment method comes from the candidate: the customer may have changed
        # cards since, and the one that matters is the one actually used.
        return {
            "amount": amount_cents,
            "currency": job.currency,
            "customer": billing.provider_customer_ref,
            "payment_method": candidate.payment_method_ref,
            "job_id": job_id,
        }

    def plausible(candidate: AuthorizationRecord) -> bool:
        """Narrow the pool before the fingerprint is asked anything.

        The fingerprint alone is not enough here, and this is the one operation
        where that is true. Every field `params_of` hashes comes from local state
        except the payment method, which the candidate supplies free — so two
        jobs posted by the same customer at the same price would hash
        identically and the sweeper would attribute one job's hold to the other.
        The candidate's own job metadata is what separates them, and the key's
        payment-method tail takes the last free field away.
        """
        if candidate.job_id != str(job_id):
            return False
        return bool(candidate.payment_method_ref) and candidate.payment_method_ref.endswith(
            pm_tail
        )

    # Cheapest first: if a payment exists for this job, its authorization is the
    # obvious candidate and one retrieve settles it — no listing, no pagination
    # limit, no lookback window.
    matches: list[AuthorizationRecord] = []
    payment = ctx.db.scalar(select(Payment).where(Payment.job_id == job_id))
    if payment is not None and payment.provider_auth_ref:
        known = ctx.provider.lookup_authorization(payment.provider_auth_ref)
        if known is not None and plausible(known):
            matches = _matching(ctx, row, [known], params_of)
    if not matches:
        matches = _matching(
            ctx,
            row,
            [
                candidate
                for candidate in ctx.provider.lookup_authorizations_for_customer(
                    billing.provider_customer_ref,
                    since=_as_utc(row.created_at) - LOOKBACK_SLACK,
                )
                if plausible(candidate)
            ],
            params_of,
        )

    if not matches:
        return _failed(
            f"the provider holds no authorization for job {job_id} at "
            f"{_money(amount_cents, job.currency)} on customer "
            f"{billing.provider_customer_ref} created since this call was recorded. "
            "The request never reached it; nothing was held and nothing was charged."
        )
    if len(matches) > 1:
        refs = ", ".join(m.ref for m in matches)
        return _unknown(
            f"{len(matches)} authorizations at the provider match this call ({refs}), so "
            "none of them can be attributed to it.",
            discrepancy=(
                f"DUPLICATE HOLDS: job {job_id} has {len(matches)} authorizations for "
                f"{_money(amount_cents, job.currency)} on customer "
                f"{billing.provider_customer_ref} — {refs}. The customer's card is held "
                "more than once for one job. Cancel the surplus holds at the provider."
            ),
        )

    auth = matches[0]
    orphan = (
        None
        if payment is not None
        else (
            f"ORPHANED HOLD: job {job_id} has an authorization {auth.ref} for "
            f"{_money(auth.amount_cents, auth.currency)} at the provider and no Payment "
            "row at all. Nothing in this system will ever capture or release it, so the "
            "customer's funds stay held until the provider expires the hold. Release it "
            "at the provider, or create the job's payment deliberately."
        )
    )
    if auth.state is AuthorizationState.HELD:
        return _succeeded(
            auth.ref,
            f"the provider holds {auth.ref} for {_money(auth.amount_cents, auth.currency)} "
            f"against job {job_id}; the authorization succeeded and the completion write "
            "is what was lost.",
            discrepancy=orphan,
        )
    if auth.state is AuthorizationState.CAPTURED:
        return _succeeded(
            auth.ref,
            f"authorization {auth.ref} exists and has since been captured, so the "
            f"authorize succeeded (charge {auth.charge_ref}).",
            discrepancy=orphan,
        )
    if auth.state is AuthorizationState.NO_HOLD:
        return _failed(
            f"authorization {auth.ref} exists at the provider but never reached a hold — "
            "the card was declined or confirmation never completed. No money moved and "
            "none ever will under this object."
        )
    if auth.state is AuthorizationState.CANCELLED:
        # Deliberately not resolved. A cancelled intent looks identical whether
        # it held funds and was released, or was declined and then cancelled,
        # and the two give opposite answers to "did the authorize succeed?".
        # Recording SUCCEEDED here would wire a dead authorization into a future
        # Payment row via the journal's replay path.
        return _unknown(
            f"authorization {auth.ref} for job {job_id} exists but is cancelled. Whether "
            "it ever held funds is not visible from its current state, so this is left "
            "pending rather than guessed. Check the authorization's history at the "
            "provider."
        )
    return _unknown(
        f"authorization {auth.ref} is still resolving at the provider. Nothing can be "
        "concluded yet; the next sweep will see a settled state."
    )


def _authorization_for(ctx: _Context, row: ProviderCall) -> tuple[Payment | None, Any]:
    """Payment + its current authorization state, for the two operations that act
    on an object we already have the reference for.

    Returns (payment, verdict-or-record). The fingerprint check is what makes
    this safe: it proves the journal row was written against the authorization
    the payment holds *now*, rather than one it has since been re-pointed at.
    """
    payment = ctx.db.get(Payment, row.payment_id)
    if payment is None:
        return None, _unknown(
            f"payment {row.payment_id} does not exist, so the authorization this call "
            "acted on is unknown and the provider cannot be asked about it."
        )
    if _fingerprint({"auth_ref": payment.provider_auth_ref}) != row.request_fingerprint:
        return payment, _unknown(
            f"this call was recorded against a different authorization than payment "
            f"{payment.id} holds today ({payment.provider_auth_ref}). Reading that "
            "authorization would answer a question nobody asked, so it is left pending."
        )
    auth = ctx.provider.lookup_authorization(payment.provider_auth_ref)
    if auth is None:
        return payment, _unknown(
            f"the provider has no authorization {payment.provider_auth_ref} for payment "
            f"{payment.id}. Without the object there is no state to read."
        )
    return payment, auth


def _resolve_capture(ctx: _Context, row: ProviderCall) -> _Verdict:
    payment, found = _authorization_for(ctx, row)
    if isinstance(found, _Verdict):
        return found
    auth: AuthorizationRecord = found

    if auth.state is AuthorizationState.CAPTURED:
        # Exactly what capture() returns: the charge if there is one, otherwise
        # the intent. Recording anything else would make the journal's replay
        # hand a different reference to the refund path than the live call did.
        ref = auth.charge_ref or auth.ref
        problems = []
        if payment.status is PaymentStatus.AUTHORIZED:
            problems.append(f"payment {payment.id} still reads '{payment.status.value}'")
        if payment.provider_charge_ref != ref:
            problems.append(
                f"provider_charge_ref is {payment.provider_charge_ref!r}, not {ref!r}"
            )
        if not _ledger_has(ctx.db, f"capture:{payment.id}"):
            problems.append(f"ledger transaction 'capture:{payment.id}' is missing")
        discrepancy = None
        if problems:
            discrepancy = (
                f"MONEY TAKEN, NOT RECORDED: payment {payment.id} (job {payment.job_id}, "
                f"customer {payment.customer_id}) was captured at the provider for "
                f"{_money(auth.amount_cents, auth.currency)} as charge {ref}, but "
                + "; ".join(problems)
                + f". The customer has paid and the worker's {payment.worker_net_cents} "
                "is not on the ledger. Post the capture through the normal path."
            )
        return _succeeded(
            ref,
            f"authorization {auth.ref} is captured at the provider for "
            f"{_money(auth.amount_cents, auth.currency)}; the capture landed and the "
            "completion write is what was lost.",
            discrepancy=discrepancy,
        )

    if auth.state is AuthorizationState.HELD:
        return _failed(
            f"authorization {auth.ref} is still an uncaptured hold at the provider, so "
            "this capture never landed. The customer has not been charged.",
            discrepancy=(
                None
                if payment.status is PaymentStatus.AUTHORIZED
                else (
                    f"CHARGED ON PAPER ONLY: payment {payment.id} reads "
                    f"'{payment.status.value}' but the provider still shows an uncaptured "
                    f"hold on {auth.ref}. The ledger has credited a worker for money that "
                    "was never taken from the customer."
                )
            ),
        )
    if auth.state is AuthorizationState.CANCELLED:
        return _failed(
            f"authorization {auth.ref} is cancelled at the provider, so this capture "
            "never landed and can never land.",
            discrepancy=(
                f"UNCAPTURABLE PAYMENT: payment {payment.id} (job {payment.job_id}) reads "
                f"'{payment.status.value}' and its authorization {auth.ref} is cancelled. "
                "The job's money cannot be collected — the customer must be re-charged "
                "through a new authorization, or the job written off."
            ),
        )
    if auth.state is AuthorizationState.NO_HOLD:
        return _failed(
            f"authorization {auth.ref} never held funds, so there was nothing to capture "
            "and nothing was charged.",
            discrepancy=(
                f"UNCAPTURABLE PAYMENT: payment {payment.id} (job {payment.job_id}) has an "
                f"authorization {auth.ref} that never held funds. Nothing can be collected "
                "against it."
            ),
        )
    return _unknown(
        f"authorization {auth.ref} is still resolving at the provider, so whether the "
        "capture landed is not yet observable."
    )


def _resolve_release(ctx: _Context, row: ProviderCall) -> _Verdict:
    payment, found = _authorization_for(ctx, row)
    if isinstance(found, _Verdict):
        return found
    auth: AuthorizationRecord = found

    if auth.state is AuthorizationState.CANCELLED:
        return _succeeded(
            # _release() returns the authorization it voided, because a
            # cancellation creates no object of its own. Same reference here.
            auth.ref,
            f"authorization {auth.ref} is cancelled at the provider; the release landed "
            "and the completion write is what was lost.",
            discrepancy=(
                None
                if payment.status is PaymentStatus.RELEASED
                else (
                    f"HOLD RELEASED, PAYMENT NOT: payment {payment.id} (job "
                    f"{payment.job_id}) reads '{payment.status.value}' while its "
                    f"authorization {auth.ref} is cancelled at the provider. Nothing can "
                    "be captured for this job."
                )
            ),
        )
    if auth.state is AuthorizationState.HELD:
        return _failed(
            f"authorization {auth.ref} is still held at the provider, so this release "
            "never landed.",
            discrepancy=(
                (
                    f"CUSTOMER STILL HELD: payment {payment.id} (job {payment.job_id}, "
                    f"customer {payment.customer_id}) reads 'released' but the provider "
                    f"still holds {_money(auth.amount_cents, auth.currency)} on "
                    f"{auth.ref}. The customer's funds are frozen on a job we consider "
                    "cancelled. Cancel the authorization."
                )
                if payment.status is PaymentStatus.RELEASED
                else None
            ),
        )
    if auth.state is AuthorizationState.CAPTURED:
        return _failed(
            f"authorization {auth.ref} was captured, not cancelled, so this release never "
            "landed.",
            discrepancy=(
                f"RELEASE ON A CAPTURED PAYMENT: payment {payment.id} (job "
                f"{payment.job_id}) reads '{payment.status.value}' and its authorization "
                f"{auth.ref} is captured (charge {auth.charge_ref}). A release was "
                "attempted against money that had already been taken — reconcile the job's "
                "state before anything else touches this payment."
            ),
        )
    if auth.state is AuthorizationState.NO_HOLD:
        return _failed(
            f"authorization {auth.ref} never held funds and is not cancelled, so this "
            "release never landed. Nothing is held either way."
        )
    return _unknown(
        f"authorization {auth.ref} is still resolving at the provider, so whether the "
        "release landed is not yet observable."
    )


def _resolve_refund(ctx: _Context, row: ProviderCall) -> _Verdict:
    payment = ctx.db.get(Payment, row.payment_id)
    if payment is None:
        return _unknown(
            f"payment {row.payment_id} does not exist, so the charge this refund was "
            "issued against is unknown."
        )
    charge_ref = payment.provider_charge_ref
    recovered = ""
    if charge_ref is None:
        # The capture's local write can be lost the same way this refund's was.
        # The authorization still knows which charge it produced, so ask it
        # rather than giving up — the fingerprint below then proves whether that
        # is really the charge this call was made against.
        auth = ctx.provider.lookup_authorization(payment.provider_auth_ref)
        charge_ref = auth.charge_ref if auth is not None else None
        recovered = " (charge reference recovered from the authorization)"
    if charge_ref is None:
        return _unknown(
            f"payment {payment.id} has no charge reference and its authorization "
            f"{payment.provider_auth_ref} reports none either, so there is no charge to "
            "list refunds against."
        )

    refunds = ctx.provider.lookup_refunds(charge_ref)
    matches = _matching(
        ctx, row, refunds, lambda r: {"charge": charge_ref, "amount": r.amount_cents}
    )
    if not matches:
        if recovered:
            # "No matching refund" is only evidence of absence if we were looking
            # at the right charge, and this charge reference was inferred rather
            # than recorded. One inference deep is not enough to tell an operator
            # "the customer was not refunded" — which is precisely the lie this
            # module exists to avoid. Say unknown instead.
            return _unknown(
                f"charge {charge_ref} carries {len(refunds)} refund(s) and none matches "
                "this call — but that charge reference was inferred from the "
                f"authorization rather than recorded on payment {payment.id}, so the "
                "absence is not proof. Restore the payment's charge reference and re-run."
            )
        return _failed(
            f"charge {charge_ref} carries {len(refunds)} refund(s) at the provider and "
            "none of them is the one this call asked for, so the refund never landed. The "
            "customer has not been paid back for this attempt."
        )
    if len(matches) > 1:
        refs = ", ".join(m.ref for m in matches)
        return _unknown(
            f"{len(matches)} refunds on charge {charge_ref} match this call ({refs}), so "
            "none can be attributed to it.",
            discrepancy=(
                f"DUPLICATE REFUNDS: charge {charge_ref} (payment {payment.id}) carries "
                f"{len(matches)} identical unclaimed refunds — {refs}. The customer has "
                "been refunded more than once for the same amount."
            ),
        )

    refund = matches[0]
    problems = []
    generation = _REFUND_KEY.match(row.idempotency_key)
    if generation is not None:
        cumulative = int(generation.group("generation")) + refund.amount_cents
        if not _ledger_has(ctx.db, f"refund:{payment.id}:{cumulative}"):
            problems.append(f"ledger transaction 'refund:{payment.id}:{cumulative}' is missing")
        if payment.refunded_cents < cumulative:
            problems.append(
                f"payment.refunded_cents is {payment.refunded_cents}, below the "
                f"{cumulative} the provider has now returned"
            )
    if not refund.settled:
        problems.append(
            f"the provider later failed or cancelled refund {refund.ref}, so the money "
            "came back to us and the customer was NOT refunded"
        )
    discrepancy = None
    if problems:
        discrepancy = (
            f"REFUND NOT RECORDED: payment {payment.id} (job {payment.job_id}, customer "
            f"{payment.customer_id}) was refunded {_money(refund.amount_cents, payment.currency)} "
            f"at the provider as {refund.ref}, but " + "; ".join(problems) + "."
        )
    return _succeeded(
        refund.ref,
        f"refund {refund.ref} for {_money(refund.amount_cents, payment.currency)} exists on "
        f"charge {charge_ref} and matches this call's recorded parameters{recovered}.",
        discrepancy=discrepancy,
    )


def _resolve_transfer(ctx: _Context, row: ProviderCall) -> _Verdict:
    payment = ctx.db.get(Payment, row.payment_id)
    if payment is None:
        return _unknown(
            f"payment {row.payment_id} does not exist, so the worker and amount this "
            "payout was for are unknown."
        )
    account = ctx.db.get(PayoutAccount, payment.worker_id)
    if account is None:
        return _unknown(
            f"worker {payment.worker_id} has no payout account, so there is no connected "
            f"account to list transfers for (payment {payment.id})."
        )
    params = {
        "destination": account.provider_account_ref,
        "amount": payment.worker_net_cents,
        "currency": payment.currency,
    }
    if _fingerprint(params) != row.request_fingerprint:
        return _unknown(
            f"this payout was requested on terms that payment {payment.id} no longer "
            f"implies — the worker's connected account ({account.provider_account_ref}) or "
            f"the net amount ({payment.worker_net_cents}) has changed since. Searching "
            "today's account for yesterday's transfer would look in the wrong place."
        )

    transfers = ctx.provider.lookup_transfers_to(
        account.provider_account_ref, since=_as_utc(row.created_at) - LOOKBACK_SLACK
    )
    for_payment = [t for t in transfers if t.payment_id == str(payment.id)]
    # Counted across claimed and unclaimed both: more than one transfer carrying
    # this payment's id is the double payout itself, whoever the second one
    # belongs to. This is the defect the journal exists to prevent, so it is
    # reported even when this particular row resolves cleanly.
    paid_twice = (
        (
            f"DOUBLE PAYOUT: payment {payment.id} (job {payment.job_id}, worker "
            f"{payment.worker_id}) has {len(for_payment)} transfers at the provider — "
            + ", ".join(f"{t.ref}={t.amount_cents}" for t in for_payment)
            + ". The worker has been paid more than once for one job."
        )
        if len(for_payment) > 1
        else None
    )
    matches = [
        t
        for t in for_payment
        if t.ref not in ctx.claimed and t.amount_cents == payment.worker_net_cents
    ]
    if not matches:
        return _failed(
            f"the provider shows no unclaimed transfer of "
            f"{_money(payment.worker_net_cents, payment.currency)} carrying payment_id "
            f"{payment.id} to {account.provider_account_ref} since this call was recorded, "
            "so the payout never landed. The worker has not been paid for it.",
            discrepancy=paid_twice,
        )
    if len(matches) > 1:
        return _unknown(
            f"{len(matches)} transfers match this payout ({', '.join(m.ref for m in matches)}), "
            "so none can be attributed to it.",
            discrepancy=paid_twice,
        )

    transfer = matches[0]
    problems = []
    if payment.status is not PaymentStatus.PAID_OUT:
        problems.append(f"payment {payment.id} still reads '{payment.status.value}'")
    if payment.provider_payout_ref != transfer.ref:
        problems.append(
            f"provider_payout_ref is {payment.provider_payout_ref!r}, not {transfer.ref!r}"
        )
    if not _ledger_has(ctx.db, f"payout:{payment.id}"):
        problems.append(f"ledger transaction 'payout:{payment.id}' is missing")
    discrepancy = paid_twice
    if problems:
        unrecorded = (
            f"WORKER PAID, NOT RECORDED: payment {payment.id} (job {payment.job_id}, worker "
            f"{payment.worker_id}) sent {_money(transfer.amount_cents, transfer.currency)} to "
            f"{account.provider_account_ref} as {transfer.ref}, but "
            + "; ".join(problems)
            + ". The worker's ledger balance still shows money we have already sent them, so "
            "a second payout attempt would pay them twice."
        )
        discrepancy = f"{paid_twice} {unrecorded}" if paid_twice else unrecorded
    return _succeeded(
        transfer.ref,
        f"transfer {transfer.ref} of {_money(transfer.amount_cents, transfer.currency)} to "
        f"{account.provider_account_ref} carries payment_id {payment.id} and matches this "
        "call's recorded terms; the payout landed.",
        discrepancy=discrepancy,
    )


def _resolve_reverse_transfer(ctx: _Context, row: ProviderCall) -> _Verdict:
    payment = ctx.db.get(Payment, row.payment_id)
    if payment is None:
        return _unknown(
            f"payment {row.payment_id} does not exist, so the transfer this claw-back "
            "reversed is unknown."
        )
    transfer_ref = payment.provider_payout_ref
    recovered = ""
    if transfer_ref is None:
        # Same recovery as the refund path: the payout's local write can be lost
        # exactly the way this reversal's was, and the transfer still carries the
        # payment id in its metadata.
        account = ctx.db.get(PayoutAccount, payment.worker_id)
        if account is not None:
            candidates = [
                t
                for t in ctx.provider.lookup_transfers_to(
                    account.provider_account_ref,
                    since=_as_utc(payment.created_at) - LOOKBACK_SLACK,
                )
                if t.payment_id == str(payment.id)
            ]
            if len(candidates) == 1:
                transfer_ref = candidates[0].ref
                recovered = " (transfer reference recovered from provider metadata)"
    if transfer_ref is None:
        return _unknown(
            f"payment {payment.id} has no payout reference and exactly one matching "
            "transfer could not be identified at the provider, so there is no transfer to "
            "list reversals against."
        )

    reversals = ctx.provider.lookup_transfer_reversals(transfer_ref)
    matches = _matching(
        ctx, row, reversals, lambda r: {"transfer": transfer_ref, "amount": r.amount_cents}
    )
    if not matches:
        if recovered:
            # Same reasoning as the refund path: an absence observed on an
            # inferred parent object is not evidence of absence.
            return _unknown(
                f"transfer {transfer_ref} carries {len(reversals)} reversal(s) and none "
                "matches this call — but that transfer reference was inferred from "
                f"provider metadata rather than recorded on payment {payment.id}, so the "
                "absence is not proof. Restore the payment's payout reference and re-run."
            )
        return _failed(
            f"transfer {transfer_ref} carries {len(reversals)} reversal(s) at the provider "
            "and none is the one this call asked for, so the claw-back never landed. The "
            "worker kept their share."
        )
    if len(matches) > 1:
        refs = ", ".join(m.ref for m in matches)
        return _unknown(
            f"{len(matches)} reversals on transfer {transfer_ref} match this call ({refs}), "
            "so none can be attributed to it.",
            discrepancy=(
                f"DOUBLE CLAW-BACK: transfer {transfer_ref} (payment {payment.id}, worker "
                f"{payment.worker_id}) carries {len(matches)} identical unclaimed reversals "
                f"— {refs}. The worker has been collected from more than once."
            ),
        )

    reversal = matches[0]
    discrepancy = None
    parsed = _REVERSAL_KEY.match(row.idempotency_key)
    if parsed is not None:
        txn_key = f"reverse:{payment.id}:{parsed.group('cumulative')}"
        if not _ledger_has(ctx.db, txn_key):
            discrepancy = (
                f"CLAW-BACK NOT RECORDED: payment {payment.id} (job {payment.job_id}, "
                f"worker {payment.worker_id}) reversed "
                f"{_money(reversal.amount_cents, payment.currency)} out of transfer "
                f"{transfer_ref} as {reversal.ref}, and ledger transaction '{txn_key}' is "
                "missing. The worker's balance reads lower than what they actually keep, so "
                "the platform's books understate what it holds."
            )
    return _succeeded(
        reversal.ref,
        f"reversal {reversal.ref} of {_money(reversal.amount_cents, payment.currency)} "
        f"exists on transfer {transfer_ref} and matches this call's recorded "
        f"parameters{recovered}.",
        discrepancy=discrepancy,
    )


_RESOLVERS: dict[str, Callable[[_Context, ProviderCall], _Verdict]] = {
    "authorize": _resolve_authorize,
    "capture": _resolve_capture,
    "release": _resolve_release,
    "refund": _resolve_refund,
    "transfer": _resolve_transfer,
    "reverse_transfer": _resolve_reverse_transfer,
}

# The journal names the operations that move money; this names the ones we can
# find out about afterwards. A seventh operation added there without a resolver
# here would be journalled and then permanently unreconcilable, so the gap is
# refused at import rather than discovered during an incident.
#
# A raise rather than an assert: asserts vanish under `python -O`, and a guard
# that disappears in the configuration you might one day run in production is
# not a guard.
if set(_RESOLVERS) != set(OPERATIONS):
    raise RuntimeError(
        "every money operation needs a reconciliation resolver; missing "
        f"{sorted(set(OPERATIONS) - set(_RESOLVERS))}, unknown "
        f"{sorted(set(_RESOLVERS) - set(OPERATIONS))}"
    )


# ---------------------------------------------------------------- the sweep


def _write(db: Session, row: ProviderCall, verdict: _Verdict, at: datetime) -> bool:
    """Record a resolution on the journal row. The only write this module makes.

    Conditioned on the row still being `pending`, because the live retry path can
    resolve it at any moment: a capture that succeeds while this sweep is reading
    the provider must not be overwritten by a verdict formed a second earlier.

    The evidence goes in `error` deliberately. A pending row never carries one —
    `error` is only ever written by journal._finish, which always sets a status
    too — so the column is free, and where a reference came from matters. A
    `succeeded` row with no provenance is indistinguishable from one the live
    call wrote, and the two deserve different amounts of trust.
    """
    status = (
        ProviderCallStatus.SUCCEEDED
        if verdict.resolution is Resolution.SUCCEEDED
        else ProviderCallStatus.FAILED
    )
    result = db.execute(
        update(ProviderCall)
        .where(
            ProviderCall.idempotency_key == row.idempotency_key,
            ProviderCall.status == ProviderCallStatus.PENDING,
        )
        .values(
            status=status,
            provider_ref=verdict.provider_ref,
            error=f"reconciled {at.isoformat()}: {verdict.detail}",
            completed_at=at,
        )
    )
    # Committed per row rather than per sweep: each resolution is an independent
    # fact, and a sweep that dies at row 150 should keep the 149 truths it has
    # already established rather than throw them away.
    db.commit()
    return bool(result.rowcount)


def reconcile_pending_calls(
    db: Session,
    *,
    dry_run: bool = True,
    older_than_minutes: int = DEFAULT_GRACE_MINUTES,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> ReconciliationReport:
    """Resolve `pending` provider-call journal rows against the provider.

    `dry_run=True` is the default everywhere it is exposed, and it writes
    nothing at all: it performs the same reads and returns the same verdicts, so
    a first run against production is incapable of changing anything while still
    showing exactly what it would change.

    Never moves money. Never touches a Payment row or the ledger. See the module
    docstring for why that boundary is where it is.
    """
    now = now or utcnow()
    cutoff = now - timedelta(minutes=max(0, older_than_minutes))

    # Counted separately from the swept rows so the `limit` budget is spent on
    # rows that can actually be judged, and so "3 calls are in flight right now"
    # is distinguishable from "nothing to do".
    in_grace = (
        db.scalar(
            select(func.count())
            .select_from(ProviderCall)
            .where(ProviderCall.status == ProviderCallStatus.PENDING)
            .where(ProviderCall.created_at > cutoff)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(ProviderCall)
            .where(ProviderCall.status == ProviderCallStatus.PENDING)
            .where(ProviderCall.created_at <= cutoff)
            .order_by(ProviderCall.created_at)
            .limit(limit)
        )
    )

    ctx = _Context(
        db=db,
        provider=_ReadOnlyProvider(get_payment_provider()),
        claimed={
            ref
            for ref in db.scalars(
                select(ProviderCall.provider_ref).where(ProviderCall.provider_ref.is_not(None))
            )
        },
    )

    outcomes: list[Outcome] = []
    for row in rows:
        resolver = _RESOLVERS.get(row.operation)
        if resolver is None:
            verdict = _unknown(
                f"'{row.operation}' is not a known money operation, so nothing here knows "
                "how to ask the provider about it."
            )
        else:
            try:
                verdict = resolver(ctx, row)
            except ProviderError as exc:
                # A provider read that fails leaves the row exactly as it was,
                # which is the correct outcome: we still do not know.
                verdict = _unknown(f"the provider could not be read: {exc}")
        outcome = Outcome(
            key=row.idempotency_key,
            operation=row.operation,
            payment_id=row.payment_id,
            resolution=verdict.resolution,
            provider_ref=verdict.provider_ref,
            detail=verdict.detail,
            discrepancy=verdict.discrepancy,
        )
        if verdict.resolution is not Resolution.UNKNOWN and not dry_run:
            outcome.written = _write(db, row, verdict, now)
            if not outcome.written:
                outcome.detail += (
                    " (not written: the row was resolved by a live retry while this sweep "
                    "was reading the provider)"
                )
        if verdict.provider_ref:
            # Claimed even in a dry run, so a second row in the same sweep cannot
            # be attributed to the same provider object and the report stays
            # identical to what an --apply run would do.
            ctx.claimed.add(verdict.provider_ref)
        if outcome.discrepancy:
            logger.error(
                "reconciliation discrepancy on %s %s: %s",
                row.operation, row.idempotency_key, outcome.discrepancy,
            )
        else:
            logger.info(
                "reconciliation %s %s -> %s: %s",
                row.operation, row.idempotency_key, verdict.resolution.value, verdict.detail,
            )
        outcomes.append(outcome)

    return ReconciliationReport(
        dry_run=dry_run,
        grace_minutes=older_than_minutes,
        in_grace_period=in_grace,
        outcomes=outcomes,
    )
