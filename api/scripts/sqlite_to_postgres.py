"""Copy development data from the SQLite file into Postgres.

One-shot migration for the dev database. Tables are copied in dependency order,
then every identity sequence is advanced past the highest id copied — without
that, Postgres hands out id 1 for the next insert and immediately collides with
the rows we just wrote.

Usage:
    .venv/bin/python scripts/sqlite_to_postgres.py \
        sqlite:///./toolbelt.db \
        postgresql+psycopg://toolbelt:toolbelt-dev-only@localhost:5432/toolbelt
"""

import sys

from sqlalchemy import create_engine, insert, select, text

sys.path.insert(0, ".")

from app.core.db import Base  # noqa: E402
from app.modules.chat import models as _chat  # noqa: F401,E402
from app.modules.identity import models as _identity  # noqa: F401,E402
from app.modules.jobs import models as _jobs  # noqa: F401,E402
from app.modules.notifications import models as _notifications  # noqa: F401,E402
from app.modules.payments import models as _payments  # noqa: F401,E402
from app.modules.reputation import models as _reputation  # noqa: F401,E402
from app.modules.trust import models as _trust  # noqa: F401,E402


def main(source_url: str, target_url: str) -> None:
    source = create_engine(source_url)
    target = create_engine(target_url)

    # sorted_tables is dependency-ordered, so parents land before children.
    tables = Base.metadata.sorted_tables

    with source.connect() as src, target.begin() as dst:
        for table in tables:
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            if not rows:
                print(f"  {table.name}: empty")
                continue
            dst.execute(insert(table), rows)
            print(f"  {table.name}: {len(rows)} rows")

        # Advance sequences past the copied ids.
        for table in tables:
            for column in table.primary_key.columns:
                if not column.autoincrement or column.type.python_type is not int:
                    continue
                seq = dst.execute(
                    text("SELECT pg_get_serial_sequence(:t, :c)"),
                    {"t": table.name, "c": column.name},
                ).scalar()
                if seq is None:
                    continue
                dst.execute(
                    text(
                        f"SELECT setval('{seq}', "
                        f"COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false)"
                    )
                )
    print("\nsequences advanced; migration complete")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
