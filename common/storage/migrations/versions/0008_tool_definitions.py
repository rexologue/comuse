"""No versions of own commands any more: a command has its current code and its draft,
and a task version keeps the definition of each tool it calls (common/tools/definition.py).

- external_tools + external_tool_versions → one external_tools, a row per state: the
  current version becomes `current`, the draft stays `draft`, older versions go; their
  folders external_tools/<group>/<action>/<n> become …/current and …/draft (the .venv is
  built again on the first call: it does not survive a move).
- Steps lose `tool_version`, cards their `version`.
- task_versions.tools: `{}` for each tool a version calls. No definition was kept before,
  so every task is looked at once: its next run is refused until its user approves it
  with the tools as they are."""

from __future__ import annotations

import json
import shutil
from typing import Any

import sqlalchemy as sa
from alembic import context, op


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    current = dict(connection.execute(sa.text("SELECT name, current_version FROM external_tools WHERE current_version IS NOT NULL")).all())
    rows = connection.execute(sa.text("SELECT * FROM external_tool_versions")).mappings().all()
    kept = []

    for row in rows:
        if row["status"] == "draft":
            kept.append({**row, "status": "draft"})
        elif current.get(row["name"]) == row["version"]:
            kept.append({**row, "status": "current"})

    op.drop_table("external_tools")
    with op.batch_alter_table("external_tool_versions") as batch_op:
        batch_op.drop_index("ix_external_tool_versions_name")
    op.drop_table("external_tool_versions")

    op.create_table(
        "external_tools",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("packages", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "status"),
    )
    with op.batch_alter_table("external_tools") as batch_op:
        batch_op.create_index("ix_external_tools_name", ["name"], unique=False)

    for row in kept:
        # As stored: the columns' text (JSON, dates) is copied, not read and written again.
        _insert(connection, "external_tools", {key: value for key, value in row.items() if key != "version"})

    with op.batch_alter_table("task_versions") as batch_op:
        batch_op.add_column(sa.Column("tools", sa.JSON(), nullable=True))

    for version_id, raw_spec, raw_cards in connection.execute(sa.text("SELECT id, spec, cards FROM task_versions")).all():
        spec = _loaded(raw_spec) or {}
        tools = {name: {} for name in sorted(_strip_versions(spec.get("steps")))}
        cards = _loaded(raw_cards) or []
        _strip_card_versions(cards)

        connection.execute(
            sa.text("UPDATE task_versions SET spec = :spec, cards = :cards, tools = :tools WHERE id = :id"),
            {"spec": json.dumps(spec, ensure_ascii=False), "cards": json.dumps(cards, ensure_ascii=False), "tools": json.dumps(tools), "id": version_id},
        )

    with op.batch_alter_table("task_versions") as batch_op:
        batch_op.alter_column("tools", existing_type=sa.JSON(), nullable=False)

    storage = context.config.attributes.get("storage")
    if storage is not None:
        _move_folders(storage.external_tools_dir, {(row["name"], row["version"]): row["status"] for row in kept})


def downgrade() -> None:
    """Back to versions: the current code is v1, the draft v2. Steps get no tool_version
    back: the tasks of an own command are to be approved again there."""
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT * FROM external_tools")).mappings().all()

    with op.batch_alter_table("task_versions") as batch_op:
        batch_op.drop_column("tools")

    with op.batch_alter_table("external_tools") as batch_op:
        batch_op.drop_index("ix_external_tools_name")
    op.drop_table("external_tools")

    op.create_table(
        "external_tool_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("packages", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
    )
    with op.batch_alter_table("external_tool_versions") as batch_op:
        batch_op.create_index("ix_external_tool_versions_name", ["name"], unique=False)

    op.create_table(
        "external_tools",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    names = {}
    for row in rows:
        number = 1 if row["status"] == "current" else 2
        _insert(connection, "external_tool_versions", {**row, "version": number, "status": "approved" if row["status"] == "current" else "draft"})

        names.setdefault(row["name"], {"name": row["name"], "current_version": None, "created_at": row["created_at"], "updated_at": row["updated_at"]})
        if row["status"] == "current":
            names[row["name"]]["current_version"] = 1

    for values in names.values():
        _insert(connection, "external_tools", values)

    storage = context.config.attributes.get("storage")
    if storage is not None:
        for row in rows:
            group, action = row["name"].split(".", 1)
            folder = storage.external_tools_dir / group / action
            source = folder / row["status"]
            if source.is_dir():
                shutil.rmtree(source / ".venv", ignore_errors=True)
                source.rename(folder / ("1" if row["status"] == "current" else "2"))


def _insert(connection, table: str, values: dict[str, Any]) -> None:
    columns = list(values)
    connection.execute(
        sa.text(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(f':{c}' for c in columns)})"),
        values,
    )


def _loaded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _strip_versions(steps: Any) -> set[str]:
    """Drop `tool_version` from the steps, nested ones too; the tools they call."""
    tools: set[str] = set()

    for step in steps or []:
        if not isinstance(step, dict):
            continue

        step.pop("tool_version", None)
        if isinstance(step.get("tool"), str) and step["tool"]:
            tools.add(step["tool"])

        tools |= _strip_versions(step.get("steps"))

    return tools


def _strip_card_versions(cards: Any) -> None:
    for card in cards or []:
        if isinstance(card, dict):
            card.pop("version", None)
            _strip_card_versions(card.get("children"))


def _move_folders(root, kept: dict[tuple[str, int], str]) -> None:
    """…/<group>/<action>/<n> → …/current or …/draft; other versions are deleted."""
    for group in [path for path in root.iterdir() if path.is_dir()] if root.is_dir() else []:
        for action in [path for path in group.iterdir() if path.is_dir()]:
            name = f"{group.name}.{action.name}"

            for folder in [path for path in action.iterdir() if path.is_dir()]:
                if not folder.name.isdigit():
                    continue

                which = kept.get((name, int(folder.name)))
                if which is None:
                    shutil.rmtree(folder, ignore_errors=True)
                    continue

                shutil.rmtree(folder / ".venv", ignore_errors=True)
                folder.rename(action / which)
