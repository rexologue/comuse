from __future__ import annotations

from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.llm.inputs import gather_text
from internal_tools.llm.model import instruct


class ClassifyInput(ToolInput):
    input: Any = Field(description="Текст или результат предыдущего шага (документ — его прочитанный text)")
    labels: list[str] = Field(min_length=2, description="Возможные категории")
    instruction: str = Field(default="", description="Как различать категории")


class ClassifyOutput(ToolOutput):
    label: str
    reason: str


@tool(
    name="llm.classify",
    title="Определить категорию",
    card="Определить категорию: {labels}",
    description="Classify a text (a document's text read by an earlier step) into exactly one of the given labels.",
    confirm=False,
)
async def classify(inp: ClassifyInput, ctx: ToolContext) -> ClassifyOutput:
    text = gather_text(inp.input, ctx, limit=20000)
    # Nothing to read is no category: a sure answer about it would be made up.
    if not text.strip():
        raise ToolError("Нет текста для классификации: шаг перед этим ничего не прочитал")

    schema = {
        "type": "object",
        "properties": {"label": {"type": "string", "enum": inp.labels}, "reason": {"type": "string"}},
        "required": ["label", "reason"],
    }

    def validate(data: Any) -> dict[str, Any]:
        if data.get("label") not in inp.labels:
            raise ValueError(f"label должен быть одним из {inp.labels}")

        return data

    data = await instruct(ctx).structured(
        [
            {
                "role": "user",
                "content": (
                    f"Отнеси документ к одной категории из {inp.labels}. {inp.instruction}\n"
                    "reason — одна короткая фраза.\n\n"
                    f"Документ:\n{text}"
                ),
            }
        ],
        schema=schema,
        name="classify",
        validate=validate,
    )

    return ClassifyOutput(label=data["label"], reason=data.get("reason", ""))
