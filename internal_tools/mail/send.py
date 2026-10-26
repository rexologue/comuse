from __future__ import annotations

import asyncio

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, ToolSettings, tool
from common.tools.fields import WorkspacePath
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.addresses import EmailAddress
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта отправителя")
    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    subject: str
    body: str
    attachments: list[WorkspacePath] = Field(default_factory=list, description="Пути файлов для вложения")


class Settings(ToolSettings):
    send_directly: bool = Field(
        default=False,
        title="Отправлять письма сразу",
        description="Выключено — письма из задачи сохраняются в черновики, отправляет их человек. Включено — задача отправляет письма сама.",
    )


class Output(ToolOutput):
    message_id: str
    # sent | draft
    mode: str
    # Where the letter is kept: Drafts for a draft, Sent for a sent letter's copy.
    folder: str | None = None
    # The letter was sent, but its copy did not get into Sent: why.
    copy_error: str | None = None
    # Recipients the server refused (address → its answer); the others got the letter.
    refused: dict[str, str] = Field(default_factory=dict)


@tool(
    name="mail.send",
    title="Отправить письмо",
    card="Отправить письмо {to} с темой «{subject}»",
    description=(
        "Send an email with attachments from a configured account. In a task it saves a draft "
        "for the user to send instead, unless the task's setting send_directly says to send; a "
        "dry run always saves a draft."
    ),
    confirm="always",
    secrets=("account",),
    timeout=120,
    settings=Settings,
)
async def send(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)

    attachments = [ctx.read_path(ref) for ref in inp.attachments]
    message = imap_smtp.build_message(
        account.config,
        to=list(inp.to),
        cc=list(inp.cc),
        subject=inp.subject,
        body=inp.body,
        attachments=attachments,
    )

    # A dry run, or a task the user has not let send by itself: the letter waits in the
    # account's Drafts, where the user reviews and sends it from their own mail client.
    if ctx.dry_run or (ctx.mode == "run" and not ctx.tool_settings.send_directly):
        folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)
        return Output(message_id=message["Message-ID"], mode="draft", folder=folder)

    refused = await asyncio.to_thread(imap_smtp.send, account.config, account.secret, message)
    sent = Output(message_id=message["Message-ID"], mode="sent", refused=refused)
    if refused:
        ctx.log(f"Сервер не принял адресатов: {', '.join(refused)}; остальным письмо ушло")

    if not imap_smtp.saves_sent_copy(account.config):
        return sent

    try:
        sent.folder = await asyncio.to_thread(imap_smtp.append_sent, account.config, account.secret, message)

    except Exception as e:
        # The letter is gone already, whatever broke after: failing here would get it
        # sent a second time.
        ctx.log(f"Письмо отправлено, но копия в «Отправленных» не сохранилась: {e}")
        sent.copy_error = str(e) or type(e).__name__

    return sent
