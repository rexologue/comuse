from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from common.external_tools import store
from common.external_tools.spec import ExternalToolSpec
from common.settings import StorageSettings
from common.storage.models import ExternalTool
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only


class Input(ToolInput):
    name: str = Field(description="Команда: группа.действие, например telegram.send")
    args: dict[str, Any] = Field(default_factory=dict, description="Аргументы, как их получит run(args, ctx)")


class Output(ToolOutput):
    output: Any


@tool(
    name="tools.try",
    title="Попробовать свою команду",
    card="Попробовать «{name}» с аргументами {args}",
    description=(
        "Run the draft of an own command once, on real arguments, to see it work before "
        "the user approves it. Installs its packages on first use. Asks the user every time: the code is not approved yet."
    ),
    confirm="always",
    chat_only=True,
)
async def trial(inp: Input, ctx: ToolContext) -> Output:
    # chat_only refuses a task and ctx.dry_run: an unapproved command runs only on a yes.
    chat_only(ctx)
    name = inp.name.strip()

    row = await asyncio.to_thread(_draft, ctx.settings.storage, name)
    spec = ExternalToolSpec.of(row, ctx.settings.storage)

    try:
        args = spec.parse(inp.args)

    except ToolError as e:
        raise ToolError(f"Аргументы не по схеме черновика: {e}") from e

    return Output(output=await spec.call(args, ctx))


def _draft(storage: StorageSettings, name: str) -> ExternalTool:
    row = store.draft_row(name)
    if row is None:
        raise ToolError(f"У `{name}` нет черновика: сначала tools.draft")

    # Files written since the draft was saved would run unchecked: save it again instead.
    if store.files_hash(store.tool_dir(storage, name, "draft")) != row.hash:
        raise ToolError("Файлы черновика изменились после сохранения: сохрани его заново (tools.draft)")

    return row
