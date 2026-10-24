from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from common.external_tools import full_name, store
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only, slug


class Input(ToolInput):
    name: str = Field(description="Команда: parse_date или ext.parse_date")
    version: int | None = Field(default=None, description="Версия; по умолчанию черновик, если он есть, иначе текущая")


class Output(ToolOutput):
    tool: str
    version: int
    status: str
    manifest: dict[str, Any]
    files: dict[str, str]
    requirements: list[str]
    packages: list[str]


@tool(
    name="tools.show",
    title="Код своей команды",
    card="Показать код «{name}»",
    description="Show an own command's version: its manifest, its files with code, its requirements and the exact packages. Use it before changing a command.",
    confirm=False,
    chat_only=True,
)
async def show(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)
    name = slug(inp.name)

    version = inp.version
    if version is None:
        found = next((t for t in await asyncio.to_thread(store.tool_list) if t["name"] == name), None)
        version = found and (found["draft_version"] or found["current_version"])

    if not version:
        raise ToolError(f"Команды `{full_name(name)}` нет")

    view = await asyncio.to_thread(store.version_view, ctx.settings.storage, name, version, files=True)

    return Output(
        tool=view["tool"],
        version=view["version"],
        status=view["status"],
        manifest=view["manifest"],
        files=view["files"],
        requirements=view["requirements"],
        packages=view["packages"],
    )
