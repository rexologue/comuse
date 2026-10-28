from __future__ import annotations

import asyncio

from pydantic import Field

from common.tools import ToolContext, ToolInput, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.addresses import EmailAddress
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.send import Output, Settings, deliver, keeps_draft


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта отправителя")
    uid: int = Field(description="UID пересылаемого письма из mail.search")
    folder: str = Field(default="INBOX", description="Папка пересылаемого письма")
    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list, description="Скрытая копия: адресаты не видят друг друга")
    note: str = Field(default="", description="Текст над пересылаемым письмом")
    include_attachments: bool = Field(default=True, description="Переслать и вложения письма")


@tool(
    name="mail.forward",
    title="Переслать письмо",
    card="Переслать письмо {uid} из «{folder}» адресатам {to}",
    description=(
        "Forward an email (its text and attachments, under an optional note) to new recipients; subject Fwd: <original>. "
        "In a task it saves a draft instead unless the task's setting send_directly says to send; a dry run always saves "
        "a draft. mode=unknown: the connection broke while sending — do not forward it again."
    ),
    confirm="always",
    secrets=("account",),
    timeout=120,
    settings=Settings,
)
async def forward(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)

    message = await asyncio.to_thread(
        imap_smtp.forward_message,
        account.config,
        account.secret,
        folder=inp.folder,
        uid=inp.uid,
        to=list(inp.to),
        cc=list(inp.cc),
        bcc=list(inp.bcc),
        note=inp.note,
        attachments=inp.include_attachments,
    )

    return await deliver(ctx, account, message, draft=keeps_draft(ctx, ctx.dry_run))
