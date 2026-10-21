"""SQLite engine shared by all services; each table has exactly one writer service."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session

from common.settings import StorageSettings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None


def init_engine(storage: StorageSettings) -> Engine:
    """Create the process-wide engine (WAL, busy timeout). The tables come from the
    migrations (common/storage/migrate.py), not from here."""
    global _engine

    storage.data_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{storage.db_path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    # Importing the models registers them on Base.metadata.
    from common.storage import models  # noqa: F401

    _engine = engine

    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Storage is not initialized; call init_engine() in the lifespan first.")

    return _engine


def session() -> Session:
    """New ORM session; objects stay usable after commit for building responses."""
    return Session(get_engine(), expire_on_commit=False)
