from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from common.tools.fields import WorkspacePath


# `{%tr for row in sales %}` and its `{%tr endfor %}`: the keys of a loop are read
# between the two, so two tables that both say `row` keep their own columns.
_LOOP_TAG = re.compile(r"\{%-?\s*(?:tr|tc|p|r)?\s*(?:for\s+([A-Za-z_]\w*)\s+in\s+([A-Za-z_]\w*)|(endfor))")


class Input(ToolInput):
    template: WorkspacePath = Field(description="Шаблон .docx с полями {{ имя }}, обычно data:templates/…")
    context: dict[str, Any] = Field(default_factory=dict, description="Значения полей шаблона")
    images: dict[str, WorkspacePath] = Field(default_factory=dict, description="Поле шаблона → путь картинки (PNG)")
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
    confirm=False,
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

    # A missing field renders empty without any error; fail loudly instead so a dry
    # run shows it to the user.
    expected = document.get_undeclared_template_variables()
    missing = sorted(expected - set(values) - set(images))
    if missing:
        raise ToolError(f"В context не хватает полей шаблона: {missing}; шаблон ждёт: {sorted(expected)}")

    _check_table_rows(document, values)

    for name, path in images.items():
        values[name] = InlineImage(document, str(path), width=Mm(160))

    try:
        document.render(values, autoescape=True)

    except Exception as e:
        raise ToolError(f"Не удалось заполнить шаблон: {e}") from e

    document.save(str(output))


def _check_table_rows(document, values: dict[str, Any]) -> None:
    """`{%tr for row in top_clients %}` … `{{ row.client }}` needs every item of
    top_clients to have `client`."""
    if document.docx is None:
        document.init_docx()

    xml = document.patch_xml(document.get_xml())

    for loop_var, list_name, body in _loops(xml):
        keys = set(re.findall(rf"\b{re.escape(loop_var)}\.([A-Za-z_][A-Za-z0-9_]*)", body))
        rows = values.get(list_name)

        if not keys or not isinstance(rows, list):
            continue

        for i, row in enumerate(rows):
            absent = sorted(keys - set(row)) if isinstance(row, dict) else sorted(keys)
            if absent:
                got = sorted(row) if isinstance(row, dict) else type(row).__name__
                raise ToolError(
                    f"Строки `{list_name}` должны иметь поля {sorted(keys)}; в строке {i + 1} нет {absent} "
                    f"(пришло: {got}). Подготовь строки шагом, который переложит их в эти поля."
                )


def _loops(xml: str) -> list[tuple[str, str, str]]:
    """(loop variable, list, the text between `for` and its `endfor`), nested loops too."""
    found: list[tuple[str, str, str]] = []
    open_loops: list[tuple[str, str, int]] = []

    for match in _LOOP_TAG.finditer(xml):
        if match.group(3) is None:
            open_loops.append((match.group(1), match.group(2), match.end()))
        elif open_loops:
            loop_var, list_name, start = open_loops.pop()
            found.append((loop_var, list_name, xml[start:match.start()]))

    # A loop with no endfor: docxtpl will say so; its keys reach to the end.
    found += [(loop_var, list_name, xml[start:]) for loop_var, list_name, start in open_loops]

    return found


def _docx_name(name: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip() or "document"

    return cleaned if cleaned.lower().endswith(".docx") else f"{cleaned}.docx"
