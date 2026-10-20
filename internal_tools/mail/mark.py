from __future__ import annotations

import asyncio

from pydantic import Field, model_validator

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")
    uid: int = Field(description="UID письма из mail.search")
    folder: str = Field(default="INBOX", description="Папка, где письмо лежит сейчас")
    seen: bool | None = Field(default=None, description="true — прочитано, false — непрочитано")
    move_to: str | None = Field(default=None, description="Переложить в эту папку (создаётся, если нет)")

    @model_validator(mode="after")
    def _something_to_do(self) -> Input:
        if self.seen is None and not self.move_to:
            raise ValueError("укажи seen и/или move_to")

        return self


class Output(ToolOutput):
    uid: int
    folder: str


@tool(
    name="mail.mark",
    title="Пометить письмо",
    card="Пометить письмо {uid} в «{account}»: прочитано — {seen}, переложить в «{move_to}»",
    description=(
        "Mark one email read/unread and/or move it to another folder. Use after handling a message "
        "so the next run does not handle it again (e.g. search unseen=true, then seen=true)."
    ),
    side_effect="write",
    secrets=("account",),
    dry_run="skip",
)
async def mark(inp: Input, ctx: ToolContext) -> Output:
    account = ctx.credential("account")

    folder = await asyncio.to_thread(
        imap_smtp.mark,
        account.config,
        account.secret,
        folder=inp.folder,
        uid=inp.uid,
        seen=inp.seen,
        move_to=inp.move_to,
    )

    return Output(uid=inp.uid, folder=folder)
