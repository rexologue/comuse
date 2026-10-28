from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath
from internal_tools.xlsx.sheets import read_sheet


class ReadInput(ToolInput):
    path: WorkspacePath = Field(description="Файл .xlsx или .csv")
    sheet: str | None = Field(default=None, description="Лист; по умолчанию первый")
    header_row: int = Field(default=1, ge=1, description="Номер строки с заголовками")
    max_rows: int = Field(default=1000, ge=1, le=50000)


class ReadOutput(ToolOutput):
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int


@tool(
    name="xlsx.read_table",
    title="Прочитать таблицу Excel",
    card="Прочитать таблицу из «{path}»",
    description=(
        "Read an Excel sheet (or a CSV) as a list of row objects keyed by the header row; merged cells hold "
        "their value in every row they cover, columns with the same header get «(2)»."
    ),
    confirm=False,
)
async def read_table(inp: ReadInput, ctx: ToolContext) -> ReadOutput:
    path = ctx.read_path(inp.path)
    if not path.is_file():
        raise ToolError(f"Файл `{inp.path}` не найден")

    return await asyncio.to_thread(_read, path, inp)


def _read(path, inp: ReadInput) -> ReadOutput:
    columns, values = read_sheet(path, inp.sheet, inp.header_row)
    rows = [{columns[i]: _jsonable(v) for i, v in enumerate(row) if i < len(columns)} for row in values[: inp.max_rows]]

    return ReadOutput(columns=columns, rows=rows, total_rows=len(values))


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    return value
