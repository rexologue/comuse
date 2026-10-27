"""What the tools.* commands share: they work in a chat only. A task runs approved
plugins; writing or trying one is a conversation with a person."""

from __future__ import annotations

import re

from common.tools import ToolContext, ToolError, discover
from common.tools.settings import tool_group
from executor.plugins.manifest import NAME_PATTERN


def chat_only(ctx: ToolContext) -> None:
    if ctx.mode != "chat" or ctx.dry_run:
        raise ToolError("Свои команды пишут и пробуют только в чате, не в задаче")


def builtin_groups() -> set[str]:
    """Groups of the built-in tools: a plugin takes a group of its own."""
    return {tool_group(name) for name in discover()}


def split(name: str) -> tuple[str, str]:
    """`telegram.send` → (plugin, tool)."""
    group, _, action = name.strip().partition(".")
    if not re.fullmatch(NAME_PATTERN, group) or not re.fullmatch(NAME_PATTERN, action):
        raise ToolError(f"Имя «{name}»: группа.действие латиницей в нижнем регистре, цифры и _, например telegram.send")

    return group, action
