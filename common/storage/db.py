"""SQLite engine shared by all services; each table has exactly one writer service."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.types import TypeDecorator

from common.settings import StorageSettings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """A moment in UTC. SQLite keeps no time zone: a value goes in as UTC and comes back
    marked UTC, so `isoformat()` says `+00:00` and no client takes it for local time."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc)

        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        return value


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
