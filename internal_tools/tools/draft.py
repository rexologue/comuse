from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import Field

from common.external_tools import store
from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only, slug


class Input(ToolInput):
    name: str = Field(description="Имя латиницей snake_case, например parse_invoice; команда станет ext.<name>")
    title: str = Field(description="Короткое название для людей")
    description: str = Field(description="Что делает команда: по этому тексту её будут выбирать")
    card: str = Field(default="", description="Что видит пользователь перед вызовом, с {аргументами}")
    confirm: bool | Literal["always"] = Field(
        default=True, description="Спрашивать ли «да» перед вызовом: false — только если команда ничего не меняет"
    )
    timeout: int | None = Field(default=None, ge=1, le=86400, description="Секунды; пусто — общий тайм-аут")
    input_schema: dict[str, Any] = Field(
        description='JSON Schema аргументов (type: object). Путь в рабочей папке — свойство с "x-path": "path"'
    )
    output_schema: dict[str, Any] = Field(description="JSON Schema ответа (type: object)")
    files: dict[str, str] = Field(
        description="Файлы: путь → текст. Обязателен main.py с def run(args, ctx), который возвращает dict"
    )
    requirements: list[str] = Field(default_factory=list, description="Пакеты из PyPI, например ['pandas>=2']")


class Output(ToolOutput):
    tool: str
    version: int
    status: str
    # The exact packages the lock resolved, transitive ones too: the user sees them.
    packages: list[str]
    next: str


@tool(
    name="tools.draft",
    title="Черновик своей команды",
    card="Записать черновик команды «{name}»",
    description=(
        "Write or rewrite the draft of an own command (ext.<name>): manifest, Python files, packages. "
        "Nothing runs: the draft is checked (schemas, syntax, packages) and waits for the user to approve it. "
        "Every problem comes back at once; fix them and call again — the draft keeps its version number. "
        "A draft written by the user or another chat is never replaced."
    ),
    confirm=False,
    chat_only=True,
)
async def draft(inp: Input, ctx: ToolContext) -> Output:
    chat_only(ctx)

    manifest = {
        "title": inp.title,
        "description": inp.description,
        "card": inp.card,
        "confirm": inp.confirm,
        "timeout": inp.timeout,
        "input_schema": inp.input_schema,
        "output_schema": inp.output_schema,
    }

    view = await asyncio.to_thread(
        store.save_draft,
        ctx.settings.storage,
        slug(inp.name),
        manifest=manifest,
        files=inp.files,
        requirements=inp.requirements,
        created_by="chat",
        session_id=ctx.session_id,
        only_own=True,
    )

    return Output(
        tool=view["tool"],
        version=view["version"],
        status=view["status"],
        packages=view["packages"],
        next=(
            "Попробуй черновик на настоящих данных (tools.try). Одобряет его только пользователь: "
            "скажи, что черновик готов и его можно посмотреть и одобрить в карточке."
        ),
    )
