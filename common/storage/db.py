"""SQLite engine shared by all services; each table has exactly one writer service."""

from __future__ import annotations

import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session

from common.settings import StorageSettings


log = logging.getLogger("storage")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None


def init_engine(storage: StorageSettings) -> Engine:
    """Create the process-wide engine (WAL, busy timeout) and the tables if missing."""
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

    # Several processes start at once and may race on CREATE TABLE; retry briefly.
    for attempt in range(1, 6):
        try:
            Base.metadata.create_all(engine)
            break

        except OperationalError as e:
            log.warning("create_all attempt %d failed: %s", attempt, e)
            time.sleep(0.5 * attempt)

    _engine = engine

    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Storage is not initialized; call init_engine() in the lifespan first.")

    return _engine


def session() -> Session:
    """New ORM session; objects stay usable after commit for building responses."""
    return Session(get_engine(), expire_on_commit=False)
