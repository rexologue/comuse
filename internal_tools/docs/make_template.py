"""Turn a filled-in example report into a reusable docxtpl template.

The LLM only points at what varies (a date, a total, the rows of a table); the
document itself is edited in code, so formatting and layout stay intact."""

from __future__ import annotations

import asyncio
import copy
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.docs.readers import markdown_table


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


class Input(ToolInput):
    example: str = Field(description="Готовый отчёт-пример .docx")
    name: str = Field(description="Имя шаблона латиницей, например weekly_sales")
    hint: str = Field(default="", description="Что в отчёте меняется от раза к разу (подсказка для разметки)")


class TemplateField(BaseModel):
    name: str
    description: str
    example: str


class TemplateTable(BaseModel):
    name: str
    columns: list[str]


class Output(ToolOutput):
    template: str
    fields: list[TemplateField]
    tables: list[TemplateTable]
    example_context: dict[str, Any]


@tool(
    name="docs.make_template",
    title="Сделать шаблон из примера",
    card="Сделать шаблон «{name}» из примера «{example}»",
    description=(
        "Create a reusable .docx template from an example report: varying values become "
        "{{ fields }}, data tables become row loops. Returns the template path, field names "
        "and the example's values as a ready context for docs.fill_template."
    ),
    side_effect="write",
    capabilities=("fs.read:{example}",),
)
async def make_template(inp: Input, ctx: ToolContext) -> Output:
    if not _NAME_RE.match(inp.name):
        raise ToolError("Имя шаблона: латиница в нижнем регистре, цифры и _, например weekly_sales")

    source = ctx.read_path(inp.example)
    if source.suffix.lower() != ".docx" or not source.is_file():
        raise ToolError(f"Нужен существующий .docx, получено `{inp.example}`")

    outline = await asyncio.to_thread(_outline, source)
    markup = await _ask_markup(ctx, outline, inp.hint)

    target = ctx.write_path(f"data:templates/{inp.name}.docx")
    target.parent.mkdir(parents=True, exist_ok=True)

    result = await asyncio.to_thread(_apply_markup, source, target, markup)
    result["template"] = ctx.to_ref(target)

    return Output.model_validate(result)


def _outline(path: Path) -> dict[str, Any]:
    from docx import Document

    document = Document(str(path))

    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    tables = [[[cell.text.strip() for cell in row.cells] for row in table.rows] for table in document.tables]

    return {"paragraphs": paragraphs, "tables": tables}


_MARKUP_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["name", "description", "example"],
            },
        },
        "tables": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "table": {"type": "integer"},
                    "name": {"type": "string"},
                    "header_rows": {"type": "integer"},
                    "columns": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["table", "name", "header_rows", "columns"],
            },
        },
    },
    "required": ["fields", "tables"],
}


async def _ask_markup(ctx: ToolContext, outline: dict[str, Any], hint: str) -> dict[str, Any]:
    text_parts = [f"P{i + 1}: {p}" for i, p in enumerate(outline["paragraphs"])]
    for i, rows in enumerate(outline["tables"]):
        text_parts.append(f"Таблица T{i + 1}:\n{markdown_table(rows)}")

    prompt = (
        "Это отчёт-пример. Найди в нём значения, которые будут меняться в следующих отчётах "
        "(даты, периоды, суммы, количества, имена в данных), и таблицы с данными, строки которых "
        "будут другими.\n"
        "- fields: name — латиницей snake_case; example — ТОЧНЫЙ фрагмент текста из документа.\n"
        "- tables: table — номер таблицы (T1 → 1); header_rows — сколько строк шапки; "
        "columns — ключи колонок латиницей snake_case по порядку.\n"
        "Постоянные заголовки и подписи не размечай.\n"
        f"{('Подсказка пользователя: ' + hint) if hint else ''}\n\n" + "\n\n".join(text_parts)
    )

    def validate(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or "fields" not in data or "tables" not in data:
            raise ValueError("ожидается объект с ключами fields и tables")

        for item in data["fields"]:
            if not _NAME_RE.match(item.get("name", "")):
                raise ValueError(f"имя поля {item.get('name')!r} должно быть snake_case латиницей")

        for item in data["tables"]:
            if not 1 <= int(item["table"]) <= len(outline["tables"]):
                raise ValueError(f"таблицы T{item['table']} нет в документе")

        return data

    return await ctx.llm.structured(
        [{"role": "user", "content": prompt}],
        model=ctx.settings.llm.model_instruct,
        schema=_MARKUP_SCHEMA,
        name="template_markup",
        validate=validate,
    )


def _apply_markup(source: Path, target: Path, markup: dict[str, Any]) -> dict[str, Any]:
    from docx import Document

    document = Document(str(source))
    example_context: dict[str, Any] = {}
    tables_out: list[dict[str, Any]] = []
    loop_tables: set[int] = set()

    for spec in markup["tables"]:
        index = int(spec["table"]) - 1
        table = document.tables[index]
        name, columns, header = spec["name"], spec["columns"], max(0, int(spec["header_rows"]))

        data_rows = table.rows[header:]
        if not data_rows or not _NAME_RE.match(name):
            continue

        example_context[name] = [
            {key: (row.cells[i].text.strip() if i < len(row.cells) else "") for i, key in enumerate(columns)}
            for row in data_rows
        ]

        _make_row_loop(table, header, name, columns)
        loop_tables.add(index)
        tables_out.append({"name": name, "columns": columns})

    paragraphs = list(document.paragraphs)
    for index, table in enumerate(document.tables):
        if index in loop_tables:
            continue

        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(cell.paragraphs)

    fields_out = []
    for field in markup["fields"]:
        example = field["example"].strip()
        if not example:
            continue

        replaced = False
        for paragraph in paragraphs:
            if example in paragraph.text:
                _set_paragraph_text(paragraph, paragraph.text.replace(example, "{{ " + field["name"] + " }}"))
                replaced = True

        if replaced:
            example_context[field["name"]] = example
            fields_out.append(field)

    if not fields_out and not tables_out:
        raise ToolError("Не нашлось ни одного изменяемого значения — шаблон не создан")

    document.save(str(target))

    return {"fields": fields_out, "tables": tables_out, "example_context": example_context}


def _make_row_loop(table, header: int, name: str, columns: list[str]) -> None:
    """Keep one data row as the pattern and wrap it in docxtpl `{%tr for %}` rows."""
    rows = table.rows
    pattern = rows[header]

    for i, cell in enumerate(pattern.cells):
        cell.text = "{{ row." + columns[i] + " }}" if i < len(columns) else ""

    for row in list(rows[header + 1:]):
        row._tr.getparent().remove(row._tr)

    open_tr = copy.deepcopy(pattern._tr)
    close_tr = copy.deepcopy(pattern._tr)
    pattern._tr.addprevious(open_tr)
    pattern._tr.addnext(close_tr)

    _set_row_tag(open_tr, "{%tr for row in " + name + " %}")
    _set_row_tag(close_tr, "{%tr endfor %}")


def _set_row_tag(tr, tag: str) -> None:
    texts = [node for node in tr.iter() if node.tag.endswith("}t")]
    for i, node in enumerate(texts):
        node.text = tag if i == 0 else ""


def _set_paragraph_text(paragraph, text: str) -> None:
    """Put all text into the first run, so a placeholder split across runs still works."""
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(text)
        return

    runs[0].text = text
    for run in runs[1:]:
        run.text = ""
