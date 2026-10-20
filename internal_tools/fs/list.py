from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath


class Input(ToolInput):
    folder: WorkspacePath = Field(default="", description="Папка относительно рабочей директории; пусто — корень")
    pattern: str = Field(default="*", description="Glob-шаблон имени, например *.xlsx")
    recursive: bool = Field(default=False, description="Искать и во вложенных папках")
    modified_from: str | None = Field(default=None, description="Изменён не раньше этой даты (YYYY-MM-DD, включительно)")
    modified_to: str | None = Field(default=None, description="Изменён не позже этой даты (YYYY-MM-DD, включительно)")


class FileInfo(BaseModel):
    path: str
    name: str
    size: int
    modified: str


class Output(ToolOutput):
    files: list[FileInfo]


@tool(
    name="fs.list",
    title="Список файлов",
    card="Взять файлы «{pattern}» из папки «{folder}»",
    description="List files in a workspace folder, filtered by a glob pattern and an optional modification date range.",
    confirm=False,
    core=True,
)
async def list_files(inp: Input, ctx: ToolContext) -> Output:
    folder = ctx.read_path(inp.folder)
    if not folder.is_dir():
        raise ToolError(f"Папка `{inp.folder}` не найдена")

    since = _parse_date(inp.modified_from, "modified_from")
    until = _parse_date(inp.modified_to, "modified_to")

    # A recursive walk over a network share takes long: off the event loop, so other
    # calls keep going and the timeout can fire.
    files = await asyncio.to_thread(_scan, folder, inp.pattern, inp.recursive, since, until, ctx)

    return Output(files=files)


def _scan(folder, pattern: str, recursive: bool, since: date | None, until: date | None, ctx: ToolContext) -> list[FileInfo]:
    matches = folder.rglob(pattern) if recursive else folder.glob(pattern)
    files: list[FileInfo] = []

    for path in sorted(matches):
        if not path.is_file():
            continue

        modified = datetime.fromtimestamp(path.stat().st_mtime)

        if since and modified < datetime.combine(since, datetime.min.time()):
            continue

        if until and modified >= datetime.combine(until + timedelta(days=1), datetime.min.time()):
            continue

        files.append(
            FileInfo(
                path=ctx.to_ref(path),
                name=path.name,
                size=path.stat().st_size,
                modified=modified.isoformat(timespec="seconds"),
            )
        )

    return files


def _parse_date(value: str | None, name: str) -> date | None:
    if not value:
        return None

    try:
        return date.fromisoformat(value[:10])

    except ValueError as e:
        raise ToolError(f"`{name}` должен быть датой YYYY-MM-DD, получено {value!r}") from e
