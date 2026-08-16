"""Grant or revoke the admin flag on a user account.

This exists because `User.is_admin` defaults to False and **nothing in the
application ever sets it** — there is no bootstrap endpoint, no seed, no
self-service path. The consequence is easy to miss and expensive: every route
behind `require_admin` is unreachable in a fresh deployment, which silently
disables the entire vetting queue (`POST /admin/vetting/{user_id}`) and dispute
resolution (`POST /admin/disputes/{id}/resolve`). A worker who submits for
vetting sits at PENDING forever, and `notify_job_posted` filters on
`vetting_status == VERIFIED`, so nobody is ever notified about a job.

This script is deliberately the *only* privilege escalation. It grants the flag
and stops there: verifying a worker, resolving a dispute, and everything else
still go through the real API, which keeps those paths exercised rather than
worked around. Resist the temptation to add a `--verify-worker` here — the
endpoint already exists, and a second back door means the first one is never
tested.

Usage
-----
    # Report only. This is the default: a first run cannot change anything.
    .venv/bin/python scripts/grant_admin.py someone@example.com

    # Actually grant it.
    .venv/bin/python scripts/grant_admin.py someone@example.com --apply

    # Take it away again.
    .venv/bin/python scripts/grant_admin.py someone@example.com --revoke --apply

    # Who currently has it?
    .venv/bin/python scripts/grant_admin.py --list

Exit codes:
    0  the account is in the requested state (changed, or already there)
    1  no such account
    2  refused: the request was ambiguous or the account cannot hold the flag

Reads TOOLBELT_DATABASE_URL from the environment or `.env`, exactly like the API
does. It prints the host and database name it is about to write to before doing
anything, because dev and production differ by one environment variable and
promoting the wrong person in the wrong place is not obvious afterwards.
"""

import argparse
import pathlib
import sys

# Absolute rather than a relative insert: this is run by hand, from whatever
# directory the operator happens to be in, and an import that silently resolves
# to the wrong tree would connect to the wrong database.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402

# Imported for the side effect of registering every mapped class. SQLAlchemy
# resolves foreign keys by table name at query time, and User's relationships
# cannot be resolved unless the modules defining their targets are imported —
# outside the app, nothing else imports them.
from app.modules.identity import models as identity_models  # noqa: F401,E402
from app.modules.jobs import models as jobs_models  # noqa: F401,E402
from app.modules.identity.models import User, UserStatus  # noqa: E402


def describe_target() -> str:
    """Host and database name, never the password.

    Printed before every action. The only thing separating the local Postgres
    from the live one is TOOLBELT_DATABASE_URL, and there is no undo.
    """
    url = make_url(get_settings().database_url)
    where = url.host or "local file"
    if url.port:
        where = f"{where}:{url.port}"
    return f"{url.drivername} · {where} · {url.database}"


def find_user(db, email: str) -> User | None:
    """Look the account up case-insensitively.

    Login lowercases the address before matching, so a stored row is normally
    lowercase — but an account created by another path may not be, and a script
    that reports "no such account" for an account that plainly exists sends the
    operator hunting in the wrong place.
    """
    return db.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


def list_admins(db) -> int:
    admins = db.scalars(select(User).where(User.is_admin.is_(True)).order_by(User.id)).all()
    if not admins:
        print("No admin accounts exist.")
        print(
            "Every route behind require_admin is currently unreachable: the vetting\n"
            "queue and dispute resolution cannot be used by anyone."
        )
        return 0
    print(f"{len(admins)} admin account(s):")
    for u in admins:
        flag = "" if u.status == UserStatus.ACTIVE else f"  [{u.status.value}]"
        print(f"  {u.id:>5}  {u.email}{flag}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grant or revoke the admin flag on a user account.",
        epilog="Without --apply this only reports what it would do.",
    )
    parser.add_argument("email", nargs="?", help="Email address of the account.")
    parser.add_argument(
        "--apply", action="store_true", help="Commit the change. Omit for a dry run."
    )
    parser.add_argument(
        "--revoke", action="store_true", help="Remove the flag instead of granting it."
    )
    parser.add_argument("--list", action="store_true", help="List current admins and exit.")
    args = parser.parse_args()

    if not args.list and not args.email:
        parser.error("an email address is required unless --list is given")
    if args.list and args.email:
        parser.error("--list takes no email address")

    print(f"database: {describe_target()}")

    with SessionLocal() as db:
        if args.list:
            return list_admins(db)

        user = find_user(db, args.email)
        if user is None:
            print(f"No account found for {args.email!r}.")
            print("Register in the app first, then run this again.")
            return 1

        wanted = not args.revoke
        verb = "grant" if wanted else "revoke"

        if user.status != UserStatus.ACTIVE and wanted:
            # A suspended account with admin rights is a strictly worse outcome
            # than no admin at all, so this refuses rather than asks.
            print(f"Refusing to {verb}: account {user.email} is {user.status.value}, not active.")
            return 2

        if user.is_admin == wanted:
            print(f"No change needed — {user.email} (id {user.id}) is already "
                  f"{'an admin' if wanted else 'not an admin'}.")
            return 0

        if not args.apply:
            print(f"Would {verb} admin on {user.email} (id {user.id}).")
            print("Nothing was written. Re-run with --apply to commit.")
            return 0

        user.is_admin = wanted
        db.commit()

        # Read it back on a fresh statement rather than trusting the in-session
        # object: the point of this script is that the row actually changed.
        db.refresh(user)
        if user.is_admin != wanted:
            print(f"Write did not stick for {user.email}. Nothing to rely on here.")
            return 2

        print(f"Done — {user.email} (id {user.id}) is now "
              f"{'an admin' if wanted else 'not an admin'}.")
        if wanted:
            print("Sign out and back in: the flag is read from the database per request,\n"
                  "but any client caching /me will still show the old value.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
