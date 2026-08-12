"""Migrations must describe the models exactly.

This exists because they once didn't: `alembic/env.py` failed to import the
trust models, so autogenerate could not see the `disputes` table and emitted an
empty migration. Nothing failed locally — the dev database is built by
`create_all()`, which creates every table the models declare — but a deployment
built from migrations had no disputes table at all.

The check below builds a database purely from migrations and asserts it matches
the models, so that drift fails here instead of in production.
"""

import pathlib

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app.core.db import Base

API_DIR = pathlib.Path(__file__).resolve().parent.parent


def _alembic_config(db_url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


@pytest.fixture()
def migrated_engine(tmp_path, monkeypatch):
    from app.core.config import get_settings

    db_path = tmp_path / "migration_check.db"
    url = f"sqlite:///{db_path}"
    # env.py resolves the URL through get_settings(), which is lru_cached and
    # already holds the suite's database. Without clearing it, migrations run
    # against the wrong database and this test silently checks nothing.
    monkeypatch.setenv("TOOLBELT_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(url), "head")
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()

    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_migrations_match_the_models(migrated_engine):
    """A database built only from migrations must match the model metadata."""
    with migrated_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)

    # Ignore index-only noise: SQLite and Postgres report these differently, and
    # a missing table — the failure this test was written for — is not an index.
    significant = [
        change
        for change in diff
        if not (isinstance(change, tuple) and str(change[0]).endswith("index"))
    ]
    assert significant == [], (
        "Models and migrations have drifted. Generate a migration:\n"
        "  .venv/bin/alembic revision --autogenerate -m 'describe change'\n"
        f"Differences: {significant}"
    )


def test_every_model_module_is_imported_by_alembic_env():
    """Autogenerate only sees tables whose modules are imported in env.py.

    A module missing here produces a silently empty migration — exactly how the
    disputes table came to be absent from deployed databases.
    """
    env_source = (API_DIR / "alembic" / "env.py").read_text()
    module_dirs = {
        path.parent.name
        for path in (API_DIR / "app" / "modules").glob("*/models.py")
    }
    missing = {
        name for name in module_dirs if f"app.modules.{name} import models" not in env_source
    }
    assert not missing, f"alembic/env.py does not import models from: {sorted(missing)}"
