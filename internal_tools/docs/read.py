from __future__ import annotations

import asyncio

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath
from common.documents import read_document


class Input(ToolInput):
    path: WorkspacePath = Field(description="Документ: docx, xlsx, pdf, txt, md, csv")
    max_chars: int = Field(default=20000, ge=100, le=200000, description="Обрезать текст до стольких символов")


class Output(ToolOutput):
    path: str
    text: str
    truncated: bool


@tool(
    name="docs.read",
    title="Прочитать документ",
    card="Прочитать «{path}»",
    description="Read a document (docx, xlsx, pdf, txt, md, csv) as text; tables become markdown tables.",
    confirm=False,
    core=True,
)
async def read(inp: Input, ctx: ToolContext) -> Output:
    path = ctx.read_path(inp.path)
    text = await asyncio.to_thread(read_document, path)

    truncated = len(text) > inp.max_chars

    return Output(path=ctx.to_ref(path), text=text[: inp.max_chars], truncated=truncated)
