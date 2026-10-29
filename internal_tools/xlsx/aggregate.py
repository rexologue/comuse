"""Deterministic totals over spreadsheet rows: the LLM never adds numbers up."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.refs import FileRefs, file_refs
from common.utils import parse_number
from internal_tools.xlsx.sheets import read_sheet

# A row of totals the table already has («Итого», «Всего»): summed again it would count
# everything twice.
# «Итого», «Итого:», «Итого по месяцу», «Total for Q3» — not «Total Logistics LLC», a client.
_TOTAL_ROW = re.compile(r"^\s*(итого|всего|итог|total|grand total)\s*(?:[:.\-–—]|\s(?:по|за|for|of)\b|$)", re.IGNORECASE)


class Input(ToolInput):
    inputs: FileRefs
    sum_columns: list[str] = Field(default_factory=list, description="Колонки, которые нужно просуммировать")
    group_by: str | None = Field(default=None, description="Колонка для группировки (например, Клиент)")
    top_n: int | None = Field(default=None, ge=1, description="Оставить N групп с наибольшей суммой первой из sum_columns")
    sheet: str | None = Field(default=None, description="Лист; по умолчанию первый")
    header_row: int = Field(default=1, ge=1, description="Номер строки с заголовками (если над таблицей есть название)")


class Group(BaseModel):
    key: str
    count: int
    sums: dict[str, float]


class Output(ToolOutput):
    files: int
    rows: int
    totals: dict[str, float]
    groups: list[Group]
    # Rows of totals the tables had («Итого»): left out of the sums.
    total_rows_skipped: int = 0


@tool(
    name="xlsx.aggregate",
    title="Посчитать итоги по таблицам",
    card="Посчитать итоги: суммы {sum_columns}, группировка «{group_by}», топ {top_n}",
    description=(
        "Compute exact totals over the rows of one or more spreadsheets: row count, sums of columns, "
        "optional group-by with per-group counts and sums, optional top-N groups. Rows of totals the table has "
        "(«Итого», «Всего») are left out. Use this for any numbers in a report instead of asking the LLM to add them up."
    ),
    confirm=False,
)
async def aggregate(inp: Input, ctx: ToolContext) -> Output:
    paths = [ctx.read_path(ref) for ref in file_refs(inp.inputs)]
    if not paths:
        raise ToolError("Нет файлов для подсчёта")

    wanted = [*inp.sum_columns, *([inp.group_by] if inp.group_by else [])]
    rows: list[dict[str, Any]] = []

    for path in paths:
        if not path.is_file():
            raise ToolError(f"Файл `{ctx.to_ref(path)}` не найден")

        header, file_rows = await asyncio.to_thread(_read_rows, path, inp.sheet, inp.header_row)

        # Every file: a column named a little differently in one of them would count as 0.
        missing = [c for c in wanted if c not in header]
        if file_rows and missing:
            raise ToolError(f"В `{ctx.to_ref(path)}` нет колонок {missing}; есть: {header}")

        rows += file_rows

    kept = [row for row in rows if not _is_total(row, inp.group_by)]
    skipped, rows = len(rows) - len(kept), kept

    totals = {column: sum(_number(row.get(column), column) for row in rows) for column in inp.sum_columns}

    groups: list[Group] = []
    if inp.group_by:
        # «ООО Ромашка» and «ООО  Ромашка » are one client: spaces and case are not a name.
        buckets: dict[str, tuple[str, list[dict[str, Any]]]] = {}
        for row in rows:
            key = _key(row.get(inp.group_by))
            buckets.setdefault(key.casefold(), (key, []))[1].append(row)

        groups = [
            Group(key=key, count=len(items), sums={c: sum(_number(r.get(c), c) for r in items) for c in inp.sum_columns})
            for key, items in buckets.values()
        ]

        if inp.sum_columns:
            groups.sort(key=lambda g: g.sums[inp.sum_columns[0]], reverse=True)

        if inp.top_n:
            groups = groups[: inp.top_n]

    return Output(files=len(paths), rows=len(rows), totals=totals, groups=groups, total_rows_skipped=skipped)


def _read_rows(path, sheet: str | None, header_row: int) -> tuple[list[str], list[dict[str, Any]]]:
    """The file's header and its rows."""
    header, values = read_sheet(path, sheet, header_row)

    return header, [dict(zip(header, row)) for row in values]


def _is_total(row: dict[str, Any], group_by: str | None) -> bool:
    """A row whose group, or one of its first text cells, says it is a total."""
    cells = [row.get(group_by)] if group_by else []
    cells += [value for value in list(row.values())[:3] if isinstance(value, str)]

    return any(isinstance(cell, str) and _TOTAL_ROW.match(cell) for cell in cells)


def _key(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "—"

    return " ".join(str(value).split())


def _number(value: Any, column: str = "") -> float:
    """An empty cell adds nothing; text that is not a number is an error, not a 0."""
    try:
        return parse_number(value) or 0.0

    except ValueError as e:
        raise ToolError(f"Значение {value!r} в колонке «{column}» не число (заголовки не в первой строке? — header_row)") from e
