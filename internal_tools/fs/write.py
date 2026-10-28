from __future__ import annotations

import asyncio

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath


class Input(ToolInput):
    path: WorkspacePath = Field(description="Путь файла в рабочей папке или data:outputs/…")
    content: str = Field(description="Текст файла (UTF-8)")
    overwrite: bool = Field(
        default=False,
        description="true — заменить файл, если он уже есть (его прежний текст пропадёт); задаче, которая каждый раз пишет один и тот же файл, — true",
    )


class Output(ToolOutput):
    path: str
    size: int


@tool(
    name="fs.write",
    title="Записать текстовый файл",
    card="Записать текст в «{path}»; заменить, если файл уже есть: {overwrite}",
    description="Write a UTF-8 text file (txt, md, csv) into the workspace. An existing file is replaced only with overwrite=true.",
)
async def write_file(inp: Input, ctx: ToolContext) -> Output:
    path = ctx.write_path(inp.path)

    if path.exists() and not inp.overwrite:
        raise ToolError(f"Файл `{inp.path}` уже существует: заменить его — overwrite=true (прежний текст пропадёт), или другое имя")

    if ctx.dry_run:
        ctx.log(f"Пробный запуск: записал бы в «{ctx.to_ref(path)}» текст ({len(inp.content)} симв.){', заменив файл' if path.exists() else ''}")
        return Output(path=ctx.to_ref(path), size=len(inp.content.encode("utf-8")))

    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, inp.content, encoding="utf-8")
    ctx.artifacts.append(ctx.to_ref(path))

    return Output(path=ctx.to_ref(path), size=path.stat().st_size)
