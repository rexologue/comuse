"""Own commands are named group.action like built-in ones: the old ones (named without a
group, shown as ext.<name>) become ext.<name>, and their folders external_tools/ext/<name>/."""

from __future__ import annotations

import shutil

import sqlalchemy as sa
from alembic import context, op


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    names = [name for (name,) in connection.execute(sa.text("SELECT name FROM external_tools")) if "." not in name]

    for name in names:
        for table in ("external_tools", "external_tool_versions"):
            connection.execute(sa.text(f"UPDATE {table} SET name = :new WHERE name = :old"), {"new": f"ext.{name}", "old": name})

    storage = context.config.attributes.get("storage")
    if storage is None:
        return

    for name in names:
        old = storage.external_tools_dir / name
        if old.is_dir():
            new = storage.external_tools_dir / "ext" / name
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))


def downgrade() -> None:
    connection = op.get_bind()

    for table in ("external_tools", "external_tool_versions"):
        connection.execute(sa.text(f"UPDATE {table} SET name = substr(name, 5) WHERE name LIKE 'ext.%'"))
