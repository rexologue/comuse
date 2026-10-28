from __future__ import annotations

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool


class NotifyInput(ToolInput):
    message: str = Field(description="Текст уведомления")


class NotifyOutput(ToolOutput):
    # False in a dry run: the message is in the run's log, not in the chat.
    sent: bool


@tool(
    name="user.notify",
    title="Уведомить пользователя",
    card="Уведомить: «{message}»",
    description="Tell the user something from a task run: it goes to the run log and to the task's chat (no email).",
    confirm=False,
)
async def notify(inp: NotifyInput, ctx: ToolContext) -> NotifyOutput:
    ctx.notify(inp.message)

    if ctx.dry_run:
        ctx.log("Пробный запуск: уведомление не отправлено в чат, оно только в журнале запуска")

    return NotifyOutput(sent=not ctx.dry_run)
