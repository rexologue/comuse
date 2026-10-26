from __future__ import annotations

import asyncio
from typing import Any

from common.external_tools import store
from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only


class Input(ToolInput):
    pass


class Output(ToolOutput):
    # name (group.action), title, current_version, draft_version, versions
    tools: list[dict[str, Any]]


@tool(
    name="tools.list",
    title="Свои команды",
    card="Показать свои команды",
    description="List the user's own commands (group.action, in groups of their own): their versions, which one is current, whether a draft waits for approval.",
    confirm=False,
    chat_only=True,
)
async def list_tools(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)

    return Output(tools=await asyncio.to_thread(store.tool_list))
