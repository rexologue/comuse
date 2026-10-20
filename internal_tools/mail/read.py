from __future__ import annotations

import asyncio

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")
    uid: int = Field(description="UID письма из mail.search")
    folder: str = Field(default="INBOX")


class Output(ToolOutput):
    uid: int
    sender: str
    sender_email: str
    to: str
    subject: str
    date: str | None
    text: str
    attachments: list[str]


@tool(
    name="mail.read",
    title="Прочитать письмо",
    card="Прочитать письмо {uid} из «{account}»",
    description="Read one email: text body and attachments (saved to data:outputs, paths returned).",
    confirm=False,
    secrets=("account",),
)
async def read(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)
    save_dir = ctx.output_dir(f"mail_{inp.uid}")

    data = await asyncio.to_thread(
        imap_smtp.read,
        account.config,
        account.secret,
        folder=inp.folder,
        uid=inp.uid,
        save_dir=save_dir,
    )

    attachments = [ctx.to_ref(p) for p in data["attachments"]]
    ctx.artifacts.extend(attachments)

    return Output(
        uid=data["uid"],
        sender=data["from"],
        sender_email=data["from_email"],
        to=data["to"],
        subject=data["subject"],
        date=data["date"],
        text=data["text"],
        attachments=attachments,
    )
