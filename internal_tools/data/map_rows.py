"""Reshape rows between steps without an LLM: a frozen task cannot ask a model to
turn `{key, sums}` from xlsx.aggregate into the `{client, amount}` a template wants."""

from __future__ import annotations

from typing import Any

from jinja2 import TemplateError
from pydantic import Field

from common import templating
from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool


class Input(ToolInput):
    rows: list[dict[str, Any]] = Field(description="Исходные строки, например groups из xlsx.aggregate")
    fields: dict[str, str] = Field(
        description=(
            "Новое поле → выражение над `row` БЕЗ фигурных скобок, например "
            "{'client': 'row.key', 'amount': \"row.sums['Сумма'] | format_number\"}"
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
        "written WITHOUT braces (row.key, row.sums['Sum'] | format_number, row.count). Optional "
        "limit keeps the first N rows. Use it to fit aggregate results to a template's table columns."
    ),
    side_effect="none",
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
