from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from executor.plugins import store
from internal_tools.tools.common import chat_only


class Input(ToolInput):
    name: str = Field(description="Плагин (группа, например telegram) или его команда (telegram.send)")
    which: Literal["draft", "current"] | None = Field(
        default=None, description="draft — черновик, current — одобренный код; по умолчанию черновик, если он есть"
    )


class Output(ToolOutput):
    plugin: str
    status: str
    manifest: dict[str, Any]
    files: dict[str, str]
    requirements: list[str]
    packages: list[str] | None


@tool(
    name="tools.show",
    title="Код плагина",
    card="Показать код «{name}»",
    description="Show a plugin's draft or approved code: its manifest (its commands with their schemas), its files, its requirements and the exact packages. Use it before changing a command.",
    confirm=False,
    chat_only=True,
)
async def show(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)
    name = inp.name.strip().split(".", 1)[0]

    which = inp.which
    if which is None:
        found = next((item for item in await asyncio.to_thread(store.plugin_list) if item["name"] == name), None)
        if found is None:
            raise ToolError(f"Плагина «{name}» нет")

        which = "draft" if found["draft"] else "current"

    view = await asyncio.to_thread(store.view, ctx.settings.storage, name, which, files=True)

    return Output(
        plugin=view["name"],
        status=view["status"],
        manifest=view["manifest"],
        files=view["files"],
        requirements=view["requirements"],
        packages=view["packages"],
    )
