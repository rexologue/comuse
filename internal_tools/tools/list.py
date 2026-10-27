from __future__ import annotations

import asyncio
from typing import Any

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from executor.plugins import store
from internal_tools.tools.common import chat_only


class Input(ToolInput):
    pass


class Output(ToolOutput):
    # name (the group), title, current and draft (None: there is none)
    plugins: list[dict[str, Any]]


@tool(
    name="tools.list",
    title="Плагины",
    card="Показать плагины",
    description="List the plugins (groups of commands besides the built-in ones, e.g. telegram with telegram.send): whether each has approved code and whether a draft waits for approval.",
    confirm=False,
    chat_only=True,
)
async def list_tools(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)

    return Output(plugins=await asyncio.to_thread(store.plugin_list))
