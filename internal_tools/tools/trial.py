from __future__ import annotations

import asyncio
from typing import Any

from pydantic import Field

from common.external_tools import full_name, store
from common.external_tools.spec import ExternalToolSpec
from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import ExternalToolVersion
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.tools.common import chat_only, slug


class Input(ToolInput):
    name: str = Field(description="Команда: parse_date или ext.parse_date")
    args: dict[str, Any] = Field(default_factory=dict, description="Аргументы, как их получит run(args, ctx)")
    version: int | None = Field(default=None, description="Версия; по умолчанию черновик")


class Output(ToolOutput):
    version: int
    output: Any


@tool(
    name="tools.try",
    title="Попробовать свою команду",
    card="Попробовать «{name}» с аргументами {args}",
    description=(
        "Run a draft (or another version) of an own command once, on real arguments, to see it work before "
        "the user approves it. Installs its packages on first use. Asks the user every time: the code is not approved yet."
    ),
    confirm="always",
    chat_only=True,
)
async def trial(inp: Input, ctx: ToolContext) -> Output:
    # chat_only refuses a task and ctx.dry_run: an unapproved command runs only on a yes.
    chat_only(ctx)
    name = slug(inp.name)

    row = await asyncio.to_thread(_version, ctx.settings.storage, name, inp.version)
    spec = ExternalToolSpec.of(row, ctx.settings.storage)

    try:
        args = spec.parse(inp.args)

    except ToolError as e:
        raise ToolError(f"Аргументы не по схеме черновика: {e}") from e

    return Output(version=row.version, output=await spec.call(args, ctx))


def _version(storage: StorageSettings, name: str, version: int | None) -> ExternalToolVersion:
    with session() as db:
        query = db.query(ExternalToolVersion).filter_by(name=name)
        row = query.filter_by(version=version).one_or_none() if version else query.filter_by(status="draft").one_or_none()

    if row is None:
        what = f"версии v{version}" if version else "черновика"
        raise ToolError(f"У `{full_name(name)}` нет {what}: сначала tools.draft")

    # Files written since the draft was saved would run unchecked: save it again instead.
    if store.files_hash(store.version_dir(storage, name, row.version)) != row.hash:
        raise ToolError("Файлы черновика изменились после сохранения: сохрани его заново (tools.draft)")

    return row
