"""Schema versions of app.db (Alembic). main brings the schema up to date before it serves
anything; every other service only waits until the schema is current, so a migration
never runs twice at once. Before migrating an existing database a copy goes to
DATA_DIR/backups.

    python -m common.storage.migrate new "add settings"   # a migration from the models

A new migration is generated against an empty database upgraded to the latest one, so
the developer's own app.db never matters."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import tempfile
import time
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from common.settings import StorageSettings
from common.storage.db import Base


log = logging.getLogger("storage")

# Alembic says "Context impl SQLiteImpl" on every check; what it did, we log ourselves.
logging.getLogger("alembic").setLevel(logging.WARNING)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# The schema create_all produced before migrations existed; such a database is stamped
# with it instead of being created again.
BASELINE = "0001"

_KEEP_BACKUPS = 5
_WAIT_STEP_SEC = 0.5


def upgrade(engine: Engine, storage: StorageSettings) -> None:
    """Bring the schema to the latest migration (main only)."""
    current, latest = current_revision(engine), head()

    if current == latest:
        return

    if current is None and _has_tables(engine):
        log.warning("database without a schema version: taking it as %s", BASELINE)
        _alembic(engine, command.stamp, BASELINE)
        current = BASELINE

        if current == latest:
            return

    if current is not None:
        _backup(storage, current)

    log.info("migrating database: %s → %s", current or "empty", latest)
    _alembic(engine, command.upgrade, "head")


def wait_current(engine: Engine, timeout: float) -> None:
    """Wait while main migrates; a service must not run on an older schema."""
    latest = head()
    deadline = time.monotonic() + timeout

    while (current := current_revision(engine)) != latest:
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Database schema is at {current or 'nothing'}, the code needs {latest}: "
                f"main migrates it on start, start main first."
            )

        time.sleep(_WAIT_STEP_SEC)


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def differences(engine: Engine) -> list:
    """What the models have that the database schema does not (empty = in sync)."""
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection), Base.metadata)


# -----------------------------
# ALEMBIC
# -----------------------------

def _config(connection=None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("file_template", "%%(rev)s_%%(slug)s")
    config.attributes["connection"] = connection

    return config


def _alembic(engine: Engine, action, revision: str) -> None:
    with engine.begin() as connection:
        action(_config(connection), revision)


def _has_tables(engine: Engine) -> bool:
    return bool(set(inspect(engine).get_table_names()) & set(Base.metadata.tables))


def _backup(storage: StorageSettings, revision: str) -> None:
    """A consistent copy (the backup API includes the WAL); the oldest copies go."""
    backups = storage.data_dir / "backups"
    backups.mkdir(exist_ok=True)

    target = backups / f"app-{revision}-{time.strftime('%Y%m%d-%H%M%S')}.db"

    with sqlite3.connect(storage.db_path) as source, sqlite3.connect(target) as copy:
        source.backup(copy)

    log.info("database backed up to %s", target)

    for old in sorted(backups.glob("app-*.db"))[:-_KEEP_BACKUPS]:
        old.unlink()


# -----------------------------
# NEW MIGRATION
# -----------------------------

def new(message: str) -> Path:
    """Generate the next numbered migration from what the models changed."""
    numbers = [int(p.name.split("_", 1)[0]) for p in (MIGRATIONS_DIR / "versions").glob("[0-9]*_*.py")]
    rev_id = f"{max(numbers, default=0) + 1:04d}"

    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'app.db'}")

        _alembic(engine, command.upgrade, "head")

        with engine.begin() as connection:
            script = command.revision(_config(connection), message, autogenerate=True, rev_id=rev_id)

        engine.dispose()

    return Path(script.path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Database migrations")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("new", help="generate a migration from the models").add_argument("message")
    args = parser.parse_args()

    print(new(args.message))
