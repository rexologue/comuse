"""Reshape rows between steps without an LLM: a frozen task cannot ask a model to
turn one step's rows (`{key, total}`) into the `{client, amount}` the next one wants."""

from __future__ import annotations

from typing import Any

from jinja2 import TemplateError
from pydantic import Field

from common import templating
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool


class Input(ToolInput):
    rows: list[dict[str, Any]] = Field(description="Исходные строки — результат предыдущего шага")
    fields: dict[str, str] = Field(
        description=(
            "Новое поле → выражение над `row` БЕЗ фигурных скобок, например "
            "{'client': 'row.name', 'amount': 'row.total | format_number'}"
        )
    )
    limit: int | None = Field(default=None, ge=1, description="Оставить первые N строк")


class Output(ToolOutput):
    rows: list[dict[str, Any]]


@tool(
    name="data.map_rows",
    title="Переложить строки",
    card="Подготовить строки с полями {fields}, не больше {limit}",
    description=(
        "Build new rows from existing ones: each output field is a Jinja expression over `row` "
        "written WITHOUT braces (row.name, row.total | format_number, row['Сумма']). Optional "
        "limit keeps the first N rows. Use it to fit one step's rows to what the next one wants."
    ),
    confirm=False,
)
async def map_rows(inp: Input, ctx: ToolContext) -> Output:
    rows = inp.rows[: inp.limit] if inp.limit else inp.rows
    out: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        item: dict[str, Any] = {}

        for name, expr in inp.fields.items():
            try:
                item[name] = templating.jsonable(templating.evaluate(expr, {"row": row}))

            except TemplateError as e:
                raise ToolError(f"Поле `{name}` = {expr!r} не вычисляется для строки {i + 1}: {e}") from e

        out.append(item)

    return Output(rows=out)
