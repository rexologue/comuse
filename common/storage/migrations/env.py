"""Alembic runs migrations only from code (common/storage/migrate.py), on the connection
it is handed; there is no alembic.ini and no offline mode."""

from alembic import context

from common.storage import models  # noqa: F401  (registers the tables on Base.metadata)
from common.storage.db import Base


# SQLite cannot alter most column properties in place: batch mode rebuilds the table.
context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=Base.metadata,
    render_as_batch=True,
)

with context.begin_transaction():
    context.run_migrations()
