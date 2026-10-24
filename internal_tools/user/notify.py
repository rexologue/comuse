from __future__ import annotations

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool


class NotifyInput(ToolInput):
    message: str = Field(description="Текст уведомления")


class NotifyOutput(ToolOutput):
    sent: bool


@tool(
    name="user.notify",
    title="Уведомить пользователя",
    card="Уведомить: «{message}»",
    description="Tell the user something from a task run: it goes to the run log and to the task's chat (no email).",
    confirm=False,
    notice_only=True,
)
async def notify(inp: NotifyInput, ctx: ToolContext) -> NotifyOutput:
    ctx.notify(inp.message)

    return NotifyOutput(sent=True)
