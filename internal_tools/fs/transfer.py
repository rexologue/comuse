"""fs.copy and fs.move share the destination rules: an existing folder (or a path
ending with `/`) receives the file under its own name."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath


class Input(ToolInput):
    src: WorkspacePath = Field(description="Файл-источник")
    dst: WorkspacePath = Field(description="Куда: папка (оканчивается на /) или новый путь файла")
    overwrite: bool = Field(default=False, description="Перезаписать, если файл уже есть")


class Output(ToolOutput):
    path: str


@tool(
    name="fs.copy",
    title="Скопировать файл",
    card="Скопировать «{src}» в «{dst}»",
    description="Copy a file inside the workspace; dst may be a folder.",
)
async def copy_file(inp: Input, ctx: ToolContext) -> Output:
    src, dst = _endpoints(inp, ctx)

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: скопировал бы «{ctx.to_ref(src)}» в «{ctx.to_ref(dst)}»")
        return Output(path=ctx.to_ref(dst))

    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copy2, src, dst)
    ctx.artifacts.append(ctx.to_ref(dst))

    return Output(path=ctx.to_ref(dst))


@tool(
    name="fs.move",
    title="Переместить файл",
    card="Переместить «{src}» в «{dst}»",
    description="Move a file inside the workspace; dst may be a folder (created if missing).",
)
async def move_file(inp: Input, ctx: ToolContext) -> Output:
    # Moving removes the source, so it needs write access to both places.
    ctx.write_path(inp.src)
    src, dst = _endpoints(inp, ctx)

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: переложил бы «{ctx.to_ref(src)}» в «{ctx.to_ref(dst)}»")
        return Output(path=ctx.to_ref(dst))

    source = ctx.to_ref(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.move, src, dst)
    ctx.moved[source] = ctx.to_ref(dst)

    return Output(path=ctx.to_ref(dst))


def _endpoints(inp: Input, ctx: ToolContext) -> tuple[Path, Path]:
    src = ctx.read_path(inp.src)
    if not src.is_file():
        raise ToolError(f"Файл `{inp.src}` не найден")

    dst = ctx.write_path(inp.dst)
    if inp.dst.endswith(("/", "\\")) or dst.is_dir():
        dst = ctx.write_path(f"{inp.dst.rstrip('/').rstrip(chr(92))}/{src.name}")

    if dst.exists() and not inp.overwrite:
        raise ToolError(f"Файл `{ctx.to_ref(dst)}` уже существует")

    return src, dst
