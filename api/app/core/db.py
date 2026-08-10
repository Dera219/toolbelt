from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    """Make a hosting provider's DATABASE_URL usable by SQLAlchemy 2 + psycopg3.

    Render, Heroku, and Railway all hand out `postgres://…`. SQLAlchemy 2
    removed that dialect alias, and the bare `postgresql://` form resolves to
    psycopg2, which is not a dependency here. Both are rewritten to the psycopg3
    driver rather than failing at startup with an unhelpful dialect error.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _make_engine():
    settings = get_settings()
    url = normalize_database_url(settings.database_url)
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
