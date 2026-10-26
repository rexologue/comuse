from __future__ import annotations

from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.llm.inputs import gather_text
from internal_tools.llm.model import instruct


class WriteInput(ToolInput):
    instruction: str = Field(description="Что написать: письмо, резюме, комментарий")
    context: Any = Field(default=None, description="Данные или текст документов (прочитанный предыдущим шагом), на которые опираться")


class WriteOutput(ToolOutput):
    text: str


@tool(
    name="llm.write",
    title="Написать текст",
    card="Написать текст: {instruction}",
    description="Write free text (an email body, a summary) from an instruction and optional context data.",
    confirm=False,
)
async def write(inp: WriteInput, ctx: ToolContext) -> WriteOutput:
    context = gather_text(inp.context, ctx, limit=30000)

    message = await instruct(ctx).complete(
        [
            {"role": "system", "content": "Пиши по-русски, деловым и простым языком. Верни только сам текст."},
            {"role": "user", "content": f"{inp.instruction}\n\n{('Данные:\n' + context) if context else ''}"},
        ],
    )

    if not message["content"]:
        raise ToolError("Модель вернула пустой текст")

    return WriteOutput(text=message["content"])
