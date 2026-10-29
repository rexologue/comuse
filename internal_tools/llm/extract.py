from __future__ import annotations

from typing import Any

import jsonschema
from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.llm.inputs import gather_text
from internal_tools.llm.model import instruct


class ExtractInput(ToolInput):
    inputs: Any = Field(description="Откуда извлекать: текст (документ — его text, прочитанный предыдущим шагом) или результат предыдущего шага")
    instruction: str = Field(description="Что именно извлечь")
    output_schema: dict[str, Any] = Field(
        description="JSON Schema результата, например {'type':'object','properties':{'revenue':{'type':'number'}}}"
    )


class ExtractOutput(ToolOutput):
    # Shaped by the step's own output_schema: the compiler checks references into it.
    data: Any = Field(json_schema_extra={"x-from-arg": "output_schema"})


@tool(
    name="llm.extract",
    title="Извлечь данные",
    card="Извлечь данные: {instruction}",
    description=(
        "Extract structured data from a text (documents read by an earlier step) with the LLM; the "
        "result follows output_schema (JSON Schema)."
    ),
    confirm=False,
)
async def extract(inp: ExtractInput, ctx: ToolContext) -> ExtractOutput:
    text = gather_text(inp.inputs, ctx)
    if not text.strip():
        raise ToolError("Нет входных данных для извлечения")

    try:
        jsonschema.Draft202012Validator.check_schema(inp.output_schema)

    except jsonschema.SchemaError as e:
        raise ToolError(f"Неверная output_schema: {e.message}") from e

    # A value the schema itself allows (an enum) may be a field's name too.
    fields = _field_names(inp.output_schema) - _allowed_values(inp.output_schema)

    def validate(data: Any) -> Any:
        try:
            jsonschema.validate(data, inp.output_schema)

        except jsonschema.ValidationError as e:
            raise ValueError(f"не соответствует схеме: {e.message}") from e

        # A model lost in a schema of strings answers with the schema's own words
        # ("qty": "price"): valid by the schema, nothing of the text.
        echoed = sorted({value for value in _strings(data) if value in fields})
        if echoed:
            raise ValueError(f"значения {echoed} — это имена полей схемы, а не данные из текста; извлеки сами значения")

        # «null» as a string reaches the document as the word.
        if any(value.strip().lower() in ("null", "none") for value in _strings(data)):
            raise ValueError("значение «null» строкой: чего в тексте нет — null без кавычек, если схема допускает, иначе пропусти поле")

        return data

    data = await instruct(ctx).structured(
        [
            {
                "role": "system",
                "content": (
                    "Извлекай данные точно по документам. Каждое поле — того типа, что в схеме: где число — число без "
                    "единиц и пробелов, где строка — значение строкой, как в документе. Чего в документе нет — null, "
                    "если схема это допускает, иначе пропусти поле. Не выдумывай отсутствующее."
                ),
            },
            {"role": "user", "content": f"Задача: {inp.instruction}\n\nДанные:\n{text}"},
        ],
        schema=inp.output_schema,
        name="extract",
        validate=validate,
    )

    return ExtractOutput(data=data)


def _field_names(schema: Any) -> set[str]:
    names: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            names.update(node.get("properties", {}) if isinstance(node.get("properties"), dict) else {})
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)

    return names


def _allowed_values(schema: Any) -> set[str]:
    """The strings an enum or a const of the schema allows."""
    if isinstance(schema, list):
        return {value for node in schema for value in _allowed_values(node)}
    if not isinstance(schema, dict):
        return set()

    own = [*schema.get("enum", []), *([schema["const"]] if "const" in schema else [])]

    return {value for value in own if isinstance(value, str)} | {value for node in schema.values() for value in _allowed_values(node)}


def _strings(data: Any) -> list[str]:
    if isinstance(data, str):
        return [data]
    if isinstance(data, dict):
        return [s for value in data.values() for s in _strings(value)]
    if isinstance(data, list):
        return [s for value in data for s in _strings(value)]
    return []
