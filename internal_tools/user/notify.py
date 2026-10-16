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
    description="Show a notification to the user in the app (no email).",
    side_effect="none",
)
async def notify(inp: NotifyInput, ctx: ToolContext) -> NotifyOutput:
    await ctx.publish("notify", {"message": inp.message, "run_id": ctx.run_id, "session_id": ctx.session_id})

    return NotifyOutput(sent=True)
