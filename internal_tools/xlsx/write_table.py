from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool


class WriteInput(ToolInput):
    rows: list[dict[str, Any]] = Field(description="Строки таблицы")
    columns: list[str] | None = Field(default=None, description="Порядок колонок; по умолчанию из первой строки")
    output_name: str = Field(default="table.xlsx")
    sheet: str = Field(default="Лист1")


class WriteOutput(ToolOutput):
    path: str
    rows: int


@tool(
    name="xlsx.write_table",
    title="Сохранить таблицу Excel",
    card="Сохранить таблицу в «{output_name}»",
    description="Write rows into a new .xlsx file under data:outputs; returns its path.",
    confirm=False,
)
async def write_table(inp: WriteInput, ctx: ToolContext) -> WriteOutput:
    name = re.sub(r"[\\/:*?\"<>|]+", "_", inp.output_name)
    path = ctx.output_path(name if name.lower().endswith(".xlsx") else f"{name}.xlsx")

    await asyncio.to_thread(_write, path, inp)

    return WriteOutput(path=ctx.to_ref(path), rows=len(inp.rows))


def _write(path, inp: WriteInput) -> None:
    from openpyxl import Workbook

    columns = inp.columns or list(inp.rows[0].keys() if inp.rows else [])

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = inp.sheet[:31]
    sheet.append(columns)

    for row in inp.rows:
        sheet.append([row.get(c) for c in columns])

    workbook.save(str(path))
