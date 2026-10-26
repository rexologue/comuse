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
    data: Any


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
    if not text:
        raise ToolError("Нет входных данных для извлечения")

    try:
        jsonschema.Draft202012Validator.check_schema(inp.output_schema)

    except jsonschema.SchemaError as e:
        raise ToolError(f"Неверная output_schema: {e.message}") from e

    def validate(data: Any) -> Any:
        try:
            jsonschema.validate(data, inp.output_schema)

        except jsonschema.ValidationError as e:
            raise ValueError(f"не соответствует схеме: {e.message}") from e

        return data

    data = await instruct(ctx).structured(
        [
            {
                "role": "system",
                "content": "Извлекай данные точно по документам. Числа — числами, без единиц и пробелов. Не выдумывай отсутствующее.",
            },
            {"role": "user", "content": f"Задача: {inp.instruction}\n\nДанные:\n{text}"},
        ],
        schema=inp.output_schema,
        name="extract",
        validate=validate,
    )

    return ExtractOutput(data=data)
