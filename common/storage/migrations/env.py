"""Alembic runs migrations only from code (common/storage/migrate.py), on the connection
it is handed; there is no alembic.ini and no offline mode."""

from alembic import context

from common.storage import models  # noqa: F401  (registers the tables on Base.metadata)
from common.storage.db import Base, UTCDateTime


# SQLite cannot alter most column properties in place: batch mode rebuilds the table.
def _render_item(kind, item, autogen_context):
    # UTCDateTime is DateTime(timezone=True) in the schema; write it so, without an
    # import of our module the migration would then depend on.
    if kind == "type" and isinstance(item, UTCDateTime):
        return "sa.DateTime(timezone=True)"

    return False


context.configure(
    connection=context.config.attributes["connection"],
    target_metadata=Base.metadata,
    render_as_batch=True,
    render_item=_render_item,
)

with context.begin_transaction():
    context.run_migrations()
