from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import Field

from common.external_tools import store
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only


class Input(ToolInput):
    name: str = Field(description="Команда: группа.действие, например telegram.send")
    which: Literal["draft", "current"] | None = Field(
        default=None, description="draft — черновик, current — одобренный код; по умолчанию черновик, если он есть"
    )


class Output(ToolOutput):
    tool: str
    status: str
    manifest: dict[str, Any]
    files: dict[str, str]
    requirements: list[str]
    packages: list[str]


@tool(
    name="tools.show",
    title="Код своей команды",
    card="Показать код «{name}»",
    description="Show an own command's draft or current code: its manifest, its files, its requirements and the exact packages. Use it before changing a command.",
    confirm=False,
    chat_only=True,
)
async def show(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)
    name = inp.name.strip()

    which = inp.which
    if which is None:
        found = next((t for t in await asyncio.to_thread(store.tool_list) if t["name"] == name), None)
        if found is None:
            raise ToolError(f"Команды `{name}` нет")

        which = "draft" if found["draft"] else "current"

    view = await asyncio.to_thread(store.view, ctx.settings.storage, name, which, files=True)

    return Output(
        tool=view["name"],
        status=view["status"],
        manifest=view["manifest"],
        files=view["files"],
        requirements=view["requirements"],
        packages=view["packages"],
    )
