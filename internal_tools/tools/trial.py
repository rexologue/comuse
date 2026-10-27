from __future__ import annotations

from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from executor.plugins.launch import try_draft
from internal_tools.tools.common import chat_only, split


class Input(ToolInput):
    name: str = Field(description="Команда черновика: группа.действие, например telegram.send")
    args: dict[str, Any] = Field(default_factory=dict, description="Аргументы, как их получит run(args, ctx)")


class Output(ToolOutput):
    output: Any


@tool(
    name="tools.try",
    title="Попробовать свою команду",
    card="Попробовать «{name}» с аргументами {args}",
    description=(
        "Run a command of a plugin's draft once, on real arguments, to see it work before "
        "the user approves it. Installs its packages on first use. Asks the user every time: the code is not approved yet."
    ),
    confirm="always",
    chat_only=True,
)
async def trial(inp: Input, ctx: ToolContext) -> Output:
    # chat_only refuses a task and ctx.dry_run: an unapproved plugin runs only on a yes.
    chat_only(ctx)
    group, action = split(inp.name)

    return Output(output=await try_draft(ctx.settings.storage, group, action, inp.args, ctx))
