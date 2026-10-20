from __future__ import annotations

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath


class Input(ToolInput):
    path: WorkspacePath = Field(description="Путь файла в рабочей папке или data:outputs/…")
    content: str = Field(description="Текст файла (UTF-8)")
    overwrite: bool = Field(default=True, description="Перезаписать существующий файл")


class Output(ToolOutput):
    path: str
    size: int


@tool(
    name="fs.write",
    title="Записать текстовый файл",
    card="Записать текст в «{path}»",
    description="Write a UTF-8 text file (txt, md, csv) into the workspace.",
    side_effect="write",
    dry_run="skip",
)
async def write_file(inp: Input, ctx: ToolContext) -> Output:
    path = ctx.write_path(inp.path)

    if path.exists() and not inp.overwrite:
        raise ToolError(f"Файл `{inp.path}` уже существует")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inp.content, encoding="utf-8")

    return Output(path=ctx.to_ref(path), size=path.stat().st_size)
