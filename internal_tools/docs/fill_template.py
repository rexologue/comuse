from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool


class Input(ToolInput):
    template: str = Field(description="Шаблон .docx с полями {{ имя }}, обычно data:templates/…")
    context: dict[str, Any] = Field(default_factory=dict, description="Значения полей шаблона")
    images: dict[str, str] = Field(default_factory=dict, description="Поле шаблона → путь картинки (PNG)")
    output_name: str = Field(default="document.docx", description="Имя готового файла")


class Output(ToolOutput):
    path: str


@tool(
    name="docs.fill_template",
    title="Заполнить шаблон документа",
    card="Заполнить шаблон «{template}» → «{output_name}»",
    description=(
        "Render a .docx template (Jinja fields like {{ revenue }}, loops {% for row in rows %}) "
        "with the given values; images are inserted from PNG paths. Returns the new file path."
    ),
    side_effect="write",
    capabilities=("fs.read:{template}",),
)
async def fill_template(inp: Input, ctx: ToolContext) -> Output:
    template = ctx.read_path(inp.template)
    if not template.is_file():
        raise ToolError(f"Шаблон `{inp.template}` не найден")

    if template.suffix.lower() != ".docx":
        raise ToolError("Поддерживаются только шаблоны .docx")

    images = {name: ctx.read_path(ref) for name, ref in inp.images.items()}
    output = ctx.output_path(_docx_name(inp.output_name))

    await asyncio.to_thread(_render, template, inp.context, images, output)

    return Output(path=ctx.to_ref(output))


def _render(template, context, images, output) -> None:
    from docx.shared import Mm
    from docxtpl import DocxTemplate, InlineImage

    document = DocxTemplate(str(template))
    values = dict(context)

    # A missing field would silently render empty; fail loudly so a dry run shows it.
    expected = document.get_undeclared_template_variables()
    missing = sorted(expected - set(values) - set(images))
    if missing:
        raise ToolError(f"В context не хватает полей шаблона: {missing}; шаблон ждёт: {sorted(expected)}")

    for name, path in images.items():
        values[name] = InlineImage(document, str(path), width=Mm(160))

    try:
        document.render(values, autoescape=True)

    except Exception as e:
        raise ToolError(f"Не удалось заполнить шаблон: {e}") from e

    document.save(str(output))


def _docx_name(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip() or "document"

    return cleaned if cleaned.lower().endswith(".docx") else f"{cleaned}.docx"
