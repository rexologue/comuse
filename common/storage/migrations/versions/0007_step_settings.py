"""Task settings move from a section per group ({"mail": {"send_directly": true}}) to what
the user changed per tool, for the whole task or a step:
{"mail": {"mail.send": {"send_directly": {"all": true}}}}.

The only setting there was is mail.send's send_directly; a default value (false) was
stored too and is dropped now: only changes are kept."""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()

    for task_id, raw in connection.execute(sa.text("SELECT id, settings FROM tasks")).all():
        old = json.loads(raw) if isinstance(raw, str) else (raw or {})
        send_directly = (old.get("mail") or {}).get("send_directly")
        new = {"mail": {"mail.send": {"send_directly": {"all": True}}}} if send_directly is True else {}

        connection.execute(sa.text("UPDATE tasks SET settings = :settings WHERE id = :id"), {"settings": json.dumps(new), "id": task_id})


def downgrade() -> None:
    connection = op.get_bind()

    for task_id, raw in connection.execute(sa.text("SELECT id, settings FROM tasks")).all():
        new = json.loads(raw) if isinstance(raw, str) else (raw or {})
        levels = ((new.get("mail") or {}).get("mail.send") or {}).get("send_directly") or {}
        old = {"mail": {"send_directly": True}} if levels.get("all") is True else {}

        connection.execute(sa.text("UPDATE tasks SET settings = :settings WHERE id = :id"), {"settings": json.dumps(old), "id": task_id})
