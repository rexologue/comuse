from __future__ import annotations

from pydantic import Field

from common.tools import NeedsInput, ToolContext, ToolInput, ToolOutput, tool


class AskInput(ToolInput):
    question: str = Field(description="Вопрос пользователю")
    options: list[str] = Field(default_factory=list, description="Варианты ответа, если есть")


class AskOutput(ToolOutput):
    answer: str


@tool(
    name="user.ask",
    title="Спросить пользователя",
    card="Спросить: «{question}»",
    description="Pause a task run and ask the user a question; the answer becomes this step's output.",
    side_effect="none",
)
async def ask(inp: AskInput, ctx: ToolContext) -> AskOutput:
    # The runner turns this into an approval and resumes the run with the answer.
    raise NeedsInput(inp.question, inp.options)
