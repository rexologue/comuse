from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.refs import FileRefs, file_refs
from internal_tools.docs.reader import read_document


class Input(ToolInput):
    path: FileRefs = Field(description="Документ (docx, xlsx, pdf, txt, md, csv), список документов или результат шага со списком файлов")
    max_chars: int = Field(default=20000, ge=100, le=200000, description="Обрезать текст каждого документа до стольких символов")


class Document(BaseModel):
    path: str
    text: str
    truncated: bool


class Output(ToolOutput):
    # The first document's path; `text` is all of them, each under «### Файл <path>»
    # when there are several (one document: its text as it is).
    path: str
    text: str
    truncated: bool
    documents: list[Document]


@tool(
    name="docs.read",
    title="Прочитать документ",
    card="Прочитать «{path}»",
    description=(
        "Read documents (docx, xlsx, pdf, txt, md, csv) as text; tables become markdown tables. "
        "Its `text` is what a model step works on."
    ),
    confirm=False,
    core=True,
)
async def read(inp: Input, ctx: ToolContext) -> Output:
    refs = file_refs(inp.path)
    if not refs:
        raise ToolError("Нет документов для чтения")

    documents = []
    for ref in refs:
        path = ctx.read_path(ref)
        text = await asyncio.to_thread(read_document, path)
        documents.append(Document(path=ctx.to_ref(path), text=text[: inp.max_chars], truncated=len(text) > inp.max_chars))

    if len(documents) == 1:
        text = documents[0].text
    else:
        text = "\n\n".join(f"### Файл {document.path}\n\n{document.text}" for document in documents)

    return Output(path=documents[0].path, text=text, truncated=any(d.truncated for d in documents), documents=documents)
