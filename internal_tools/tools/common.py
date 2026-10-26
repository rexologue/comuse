"""What the tools.* commands share: they work in a chat only. A task runs approved
commands; writing or trying one is a conversation with a person."""

from __future__ import annotations

from common.tools import ToolContext, ToolError, discover
from common.tools.settings import tool_group


def chat_only(ctx: ToolContext) -> None:
    if ctx.mode != "chat" or ctx.dry_run:
        raise ToolError("Свои команды пишут и пробуют только в чате, не в задаче")


def builtin_groups() -> set[str]:
    """Groups of the built-in tools: an own command takes a group of its own."""
    return {tool_group(name) for name in discover()}
