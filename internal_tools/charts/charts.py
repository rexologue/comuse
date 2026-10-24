from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.utils import parse_number


class Input(ToolInput):
    data: list[dict[str, Any]] = Field(description="Строки данных")
    label_key: str = Field(description="Поле с подписью (ось X)")
    value_key: str = Field(description="Поле с числом (ось Y)")
    title: str = Field(default="", description="Заголовок графика")
    output_name: str = Field(default="chart.png")


class Output(ToolOutput):
    path: str


@tool(
    name="charts.bar",
    title="Столбчатая диаграмма",
    card="Построить диаграмму «{title}»",
    description="Draw a bar chart PNG from rows (label_key on X, value_key on Y); returns the image path.",
    confirm=False,
)
async def bar(inp: Input, ctx: ToolContext) -> Output:
    return await _draw(inp, ctx, "bar")


@tool(
    name="charts.line",
    title="Линейный график",
    card="Построить график «{title}»",
    description="Draw a line chart PNG from rows (label_key on X, value_key on Y); returns the image path.",
    confirm=False,
)
async def line(inp: Input, ctx: ToolContext) -> Output:
    return await _draw(inp, ctx, "line")


async def _draw(inp: Input, ctx: ToolContext, kind: Literal["bar", "line"]) -> Output:
    labels, values = [], []

    for row in inp.data:
        if inp.label_key not in row or inp.value_key not in row:
            raise ToolError(f"В строке нет полей `{inp.label_key}` / `{inp.value_key}`: {row}")

        labels.append(str(row[inp.label_key]))
        values.append(_number(row[inp.value_key]))

    path = ctx.output_path(inp.output_name, suffix=".png")

    await asyncio.to_thread(_render, path, kind, labels, values, inp.title)

    return Output(path=ctx.to_ref(path))


def _render(path, kind, labels, values, title) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)

    if kind == "bar":
        ax.bar(labels, values, color="#3b6fd8")
    else:
        ax.plot(labels, values, marker="o", color="#3b6fd8")

    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    # On these axes, not pyplot's current ones: charts of other runs render at the same time.
    ax.tick_params(axis="x", labelrotation=30)
    plt.setp(ax.get_xticklabels(), ha="right")
    fig.tight_layout()
    fig.savefig(str(path))
    plt.close(fig)


def _number(value: Any) -> float:
    try:
        number = parse_number(value)

    except ValueError:
        number = None

    if number is None:
        raise ToolError(f"Значение {value!r} не число")

    return number
