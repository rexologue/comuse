from __future__ import annotations

import asyncio

from pydantic import EmailStr, Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта отправителя")
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = Field(default_factory=list)
    subject: str
    body: str
    attachments: list[str] = Field(default_factory=list, description="Пути файлов для вложения")


class Output(ToolOutput):
    message_id: str
    # sent | draft
    mode: str
    folder: str | None = None


@tool(
    name="mail.send",
    title="Отправить письмо",
    card="Отправить письмо {to} с темой «{subject}»",
    description="Send an email with attachments from a configured account.",
    side_effect="external",
    capabilities=("mail.account:{account}", "mail.send:{to}", "mail.send:{cc}", "fs.read:{attachments}"),
    secrets=("account",),
    dry_run="draft",
    timeout=120,
)
async def send(inp: Input, ctx: ToolContext) -> Output:
    account = ctx.account(inp.account)
    ctx.check_recipients([*inp.to, *inp.cc])

    attachments = [ctx.read_path(ref) for ref in inp.attachments]
    message = imap_smtp.build_message(
        account.config,
        to=list(inp.to),
        cc=list(inp.cc),
        subject=inp.subject,
        body=inp.body,
        attachments=attachments,
    )

    # Draft mode (dry run, first runs of a task): the user finds it in their own
    # mail client's Drafts and can review it there.
    if ctx.draft:
        folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)
        return Output(message_id=message["Message-ID"], mode="draft", folder=folder)

    await asyncio.to_thread(imap_smtp.send, account.config, account.secret, message)

    return Output(message_id=message["Message-ID"], mode="sent")
