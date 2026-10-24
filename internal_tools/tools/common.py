"""What the tools.* commands share: they work in a chat only. A task runs approved
commands; writing or trying one is a conversation with a person."""

from __future__ import annotations

from common.external_tools import PREFIX
from common.tools import ToolContext, ToolError


def chat_only(ctx: ToolContext) -> None:
    if ctx.mode != "chat" or ctx.dry_run:
        raise ToolError("Свои команды пишут и пробуют только в чате, не в задаче")


def slug(name: str) -> str:
    """`ext.parse_date` or `parse_date` → `parse_date`."""
    return name.strip().removeprefix(PREFIX)
