"""Resolve `pending` provider-call journal rows against the payment provider.

A journal row is written before a provider call and closed out after it. A crash
in between leaves the row `pending`, and until this script existed `pending` meant
"unknown" permanently: nothing ever asked the provider what actually happened.

This asks. It reads — it never replays a call, never moves money, never edits a
Payment row or the ledger. Where the provider's truth and our books disagree it
prints a DISCREPANCY for a human instead of repairing it. See
`app/modules/payments/reconcile.py` for why that boundary is where it is.

Usage
-----
    # Report only. This is the default: a first run cannot change anything.
    .venv/bin/python scripts/reconcile_provider_calls.py

    # Same sweep, writing the resolutions it finds into the journal.
    .venv/bin/python scripts/reconcile_provider_calls.py --apply

    # Wider window, machine-readable output.
    .venv/bin/python scripts/reconcile_provider_calls.py --older-than-minutes 60 --json

Exit codes, for wiring to a Render Cron Job with no rework:
    0  nothing needs a human
    1  at least one row could not be resolved (UNKNOWN)
    2  at least one DISCREPANCY: the provider and our books disagree about money
       (2 wins when both are present)

Reads TOOLBELT_DATABASE_URL and TOOLBELT_STRIPE_SECRET_KEY from the environment
or `.env`, exactly like the API does. With no Stripe key configured it runs
against FakePaymentProvider, which is useful in dev and meaningless in prod.
"""

import argparse
import json
import pathlib
import sys

# Absolute rather than `sys.path.insert(0, ".")` as the sibling migration script
# uses: a cron job's working directory is whatever the platform chooses, and a
# reconciliation run that silently fails to import is a reconciliation run that
# never happened.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal  # noqa: E402

# Imported for the side effect of registering every mapped class, exactly as
# scripts/sqlite_to_postgres.py does. SQLAlchemy resolves foreign keys by table
# name at query time, and `payments.job_id -> jobs.id` cannot be resolved unless
# the jobs module has been imported — outside the app, nothing else imports it.
from app.modules.chat import models as _chat  # noqa: F401,E402
from app.modules.identity import models as _identity  # noqa: F401,E402
from app.modules.jobs import models as _jobs  # noqa: F401,E402
from app.modules.notifications import models as _notifications  # noqa: F401,E402
from app.modules.payments import models as _payments  # noqa: F401,E402
from app.modules.payments import reconcile  # noqa: E402
from app.modules.payments.reconcile import Resolution  # noqa: E402
from app.modules.reputation import models as _reputation  # noqa: F401,E402
from app.modules.trust import models as _trust  # noqa: F401,E402


def _render(report: reconcile.ReconciliationReport) -> None:
    mode = "DRY RUN — nothing was written" if report.dry_run else "APPLIED"
    print(f"Provider-call reconciliation — {mode}")
    print(
        f"  scanned {report.scanned} pending row(s) older than {report.grace_minutes} min; "
        f"{report.in_grace_period} still inside the grace period"
    )
    print(
        f"  resolved: {report.count(Resolution.SUCCEEDED)} succeeded, "
        f"{report.count(Resolution.FAILED)} failed, "
        f"{report.count(Resolution.UNKNOWN)} unknown"
    )
    for outcome in report.outcomes:
        ref = f" -> {outcome.provider_ref}" if outcome.provider_ref else ""
        # An UNKNOWN row is never meant to be written, so flagging it as "not
        # written" would read as a failure. The marker is only for a verdict that
        # *should* have landed and did not — a live retry resolved the row while
        # the sweep was reading the provider.
        stale = (
            outcome.resolution is not Resolution.UNKNOWN
            and not report.dry_run
            and not outcome.written
        )
        written = "  [not written: resolved by a live retry mid-sweep]" if stale else ""
        print(f"\n  [{outcome.resolution.value.upper()}] {outcome.operation} {outcome.key}{ref}{written}")
        print(f"      {outcome.detail}")
        if outcome.discrepancy:
            # Indented under its row but shouted, because this is the line that
            # is worth waking someone for. Everything needed to act is in it.
            print(f"      !! DISCREPANCY: {outcome.discrepancy}")
    if report.discrepancies:
        print(
            f"\n{len(report.discrepancies)} discrepancy(ies) need a human. Nothing was "
            "corrected automatically — the sweeper relabels journal rows and never moves "
            "money or edits the ledger."
        )
    if report.unresolved:
        print(
            f"{len(report.unresolved)} row(s) could not be resolved and stay pending. "
            "That is a real answer, not a failure: re-run after the provider settles, or "
            "read the detail above for what is missing."
        )


def _as_json(report: reconcile.ReconciliationReport) -> str:
    return json.dumps(
        {
            "dry_run": report.dry_run,
            "grace_minutes": report.grace_minutes,
            "scanned": report.scanned,
            "in_grace_period": report.in_grace_period,
            "succeeded": report.count(Resolution.SUCCEEDED),
            "failed": report.count(Resolution.FAILED),
            "unknown": report.count(Resolution.UNKNOWN),
            "discrepancy_count": len(report.discrepancies),
            "outcomes": [
                {
                    "key": o.key,
                    "operation": o.operation,
                    "payment_id": o.payment_id,
                    "resolution": o.resolution.value,
                    "provider_ref": o.provider_ref,
                    "detail": o.detail,
                    "discrepancy": o.discrepancy,
                    "written": o.written,
                }
                for o in report.outcomes
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile pending provider-call journal rows against the provider.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "write the resolutions into the journal. Omitted, the run is a dry run and "
            "changes nothing — which is the default deliberately, so a first run against "
            "production is incapable of doing harm."
        ),
    )
    parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=reconcile.DEFAULT_GRACE_MINUTES,
        help=(
            "ignore rows younger than this; they are plausibly still in flight and "
            f"judging them races the live call (default {reconcile.DEFAULT_GRACE_MINUTES})"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=reconcile.DEFAULT_LIMIT,
        help=f"maximum rows to judge in one sweep (default {reconcile.DEFAULT_LIMIT})",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    if args.older_than_minutes < 0:
        parser.error("--older-than-minutes cannot be negative")

    with SessionLocal() as db:
        report = reconcile.reconcile_pending_calls(
            db,
            dry_run=not args.apply,
            older_than_minutes=args.older_than_minutes,
            limit=args.limit,
        )

    if args.json:
        print(_as_json(report))
    else:
        _render(report)

    if report.discrepancies:
        return 2
    if report.unresolved:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
