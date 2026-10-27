"""Own commands become plugins: one plugin per group, its commands its tools.

Every group of external_tools becomes a plugin of functions, a draft: each command (its
draft if it has one, else its approved code) goes to plugins/<group>/draft/<action>/,
its tool.json into the plugin's plugin.json, its requirements into the plugin's. A person approves the plugin as a whole: it is code put
together anew, and a new way to run it. Its packages are resolved by the executor when
it starts (store.finish_draft): a migration has no business going to the network.

The steps of tasks keep their tools' names (ext.parse_date stays ext.parse_date); their
definitions change (other code), so those tasks wait for their user, as with any new code.

Down: the table external_tools comes back empty; the plugins' rows and files stay."""

from __future__ import annotations

import json
import shutil
from typing import Any

import sqlalchemy as sa
from alembic import context, op


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


_BOOKKEEPING = {"tool.json", "requirements.txt", "requirements.lock", ".venv"}


def upgrade() -> None:
    connection = op.get_bind()
    commands = connection.execute(sa.text("SELECT * FROM external_tools ORDER BY name, status")).mappings().all()

    # Per command: its draft, else its approved code.
    chosen: dict[str, Any] = {}
    for row in commands:
        if row["name"] not in chosen or row["status"] == "draft":
            chosen[row["name"]] = row

    op.create_table(
        "plugins",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("hash", sa.String(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("packages", sa.JSON(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("tools_hash", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "status"),
    )
    with op.batch_alter_table("plugins") as batch_op:
        batch_op.create_index("ix_plugins_name", ["name"], unique=False)

    groups: dict[str, list[Any]] = {}
    for name, row in sorted(chosen.items()):
        groups.setdefault(name.split(".", 1)[0], []).append(row)

    storage = context.config.attributes.get("storage")

    for group, rows in groups.items():
        tools = []
        requirements: list[str] = []

        for row in rows:
            action = row["name"].split(".", 1)[1]
            manifest = _loaded(row["manifest"]) or {}
            tools.append({
                "name": action,
                "title": manifest.get("title") or action,
                "description": manifest.get("description") or action,
                "card": manifest.get("card") or "",
                "confirm": manifest.get("confirm", True),
                "timeout": manifest.get("timeout"),
                "input_schema": manifest.get("input_schema") or {"type": "object", "properties": {}},
                "output_schema": manifest.get("output_schema") or {"type": "object"},
                "entry": f"{action}/main.py",
            })

            if storage is not None:
                requirements += _move_command(storage, group, action, row["status"])

        plugin = {"title": group, "description": "", "tools": [{k: v for k, v in tool.items() if v is not None} for tool in tools]}
        requirements = list(dict.fromkeys(requirements))

        if storage is not None:
            draft = storage.plugins_dir / group / "draft"
            (draft / "plugin.json").write_text(json.dumps(plugin, ensure_ascii=False, indent=2), encoding="utf-8")
            (draft / "requirements.txt").write_text("".join(f"{line}\n" for line in requirements), encoding="utf-8")

        first = rows[0]
        connection.execute(
            sa.text(
                "INSERT INTO plugins (id, name, status, hash, manifest, packages, tools, tools_hash, created_by, session_id, created_at, updated_at) "
                "VALUES (:id, :name, 'draft', '', :manifest, NULL, NULL, NULL, :created_by, :session_id, :created_at, :updated_at)"
            ),
            {
                "id": f"plg_{group}",
                "name": group,
                "manifest": json.dumps(plugin, ensure_ascii=False),
                "created_by": first["created_by"],
                "session_id": first["session_id"],
                "created_at": first["created_at"],
                "updated_at": first["updated_at"],
            },
        )

    with op.batch_alter_table("external_tools") as batch_op:
        batch_op.drop_index("ix_external_tools_name")
    op.drop_table("external_tools")

    if storage is not None:
        shutil.rmtree(storage.external_tools_dir, ignore_errors=True)


def downgrade() -> None:
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

    with op.batch_alter_table("plugins") as batch_op:
        batch_op.drop_index("ix_plugins_name")
    op.drop_table("plugins")


def _move_command(storage, group: str, action: str, status: str) -> list[str]:
    """A command's files into the plugin's draft, under <action>/; its requirements."""
    source = storage.external_tools_dir / group / action / status
    target = storage.plugins_dir / group / "draft" / action
    target.mkdir(parents=True, exist_ok=True)

    requirements: list[str] = []
    if not source.is_dir():
        return requirements

    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if rel.parts[0] in _BOOKKEEPING or "__pycache__" in rel.parts or not path.is_file():
            continue

        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target / rel)

    listed = source / "requirements.txt"
    if listed.is_file():
        requirements = [line.strip() for line in listed.read_text(encoding="utf-8").splitlines() if line.strip()]

    return requirements


def _loaded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value
