from __future__ import annotations

import asyncio
from email.message import EmailMessage

from pydantic import Field

from common.tools import Credential, ToolContext, ToolInput, ToolOutput, ToolSettings, tool
from common.tools.fields import WorkspacePath
from internal_tools.mail.accounts import check_recipients, mailbox
from internal_tools.mail.addresses import EmailAddress
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта отправителя")
    to: list[EmailAddress] = Field(min_length=1)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list, description="Скрытая копия: адресаты не видят друг друга")
    subject: str
    body: str
    attachments: list[WorkspacePath] = Field(default_factory=list, description="Пути файлов для вложения")
    in_reply_to: int | None = Field(
        default=None,
        description="UID письма, на которое это ответ (из mail.search): письмо встанет в его цепочку, к теме добавится Re:",
    )
    in_reply_to_folder: str = Field(default="INBOX", description="Папка письма из in_reply_to")
    reply_to: EmailAddress | None = Field(default=None, description="Куда отвечать на это письмо, если не отправителю")


class Settings(ToolSettings):
    send_directly: bool = Field(
        default=False,
        title="Отправлять письма сразу",
        description="Выключено — письма из задачи сохраняются в черновики, отправляет их человек. Включено — задача отправляет письма сама.",
    )


class Output(ToolOutput):
    message_id: str
    # sent | draft | unknown (the connection broke while the letter was going)
    mode: str
    # Where the letter is kept: Drafts for a draft, Sent for a sent letter's copy.
    folder: str | None = None
    # The letter was sent, but its copy did not get into Sent: why.
    copy_error: str | None = None
    # Recipients the server refused (address → its answer); the others got the letter.
    refused: dict[str, str] = Field(default_factory=dict)


async def compose(inp: Input, ctx: ToolContext, account: Credential) -> EmailMessage:
    check_recipients(account, inp.to, inp.cc, inp.bcc)

    parent = None
    if inp.in_reply_to is not None:
        parent = await asyncio.to_thread(imap_smtp.thread_of, account.config, account.secret, folder=inp.in_reply_to_folder, uid=inp.in_reply_to)

    return imap_smtp.build_message(
        account.config,
        to=list(inp.to),
        cc=list(inp.cc),
        bcc=list(inp.bcc),
        reply_to=inp.reply_to,
        subject=inp.subject,
        body=inp.body,
        attachments=[ctx.read_path(ref) for ref in inp.attachments],
        parent=parent,
    )


def keeps_draft(ctx: ToolContext, dry_run: bool) -> bool:
    """A dry run, or a task the user has not let send by itself: the letter waits in the
    account's Drafts, where the user reviews and sends it from their own mail client."""
    return dry_run or (ctx.mode == "run" and not ctx.tool_settings.send_directly)


async def deliver(ctx: ToolContext, account: Credential, message: EmailMessage, *, draft: bool, answers: tuple[str, int] | None = None) -> Output:
    """Send the letter, or keep it in Drafts (`draft`, keeps_draft); `answers` (folder,
    uid): the letter it replies to, marked answered once this one is gone."""
    if draft:
        folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)
        return Output(message_id=message["Message-ID"], mode="draft", folder=folder)

    try:
        refused = await asyncio.to_thread(imap_smtp.send, account.config, account.secret, message)

    except imap_smtp.DeliveryUnknown as e:
        # Not an error to retry: sending again may deliver the letter twice.
        ctx.notify(f"Неизвестно, ушло ли письмо «{message['Subject']}»: {e}. Проверьте у адресатов, прежде чем отправлять снова.")
        return Output(message_id=message["Message-ID"], mode="unknown")

    sent = Output(message_id=message["Message-ID"], mode="sent", refused=refused)
    if refused:
        ctx.log(f"Сервер не принял адресатов: {', '.join(refused)}; остальным письмо ушло")

    # The letter is gone already, whatever breaks after: failing here would get it sent a second time.
    if answers is not None:
        try:
            await asyncio.to_thread(imap_smtp.set_flag, account.config, account.secret, folder=answers[0], uid=answers[1], flag=r"\Answered")

        except Exception as e:
            ctx.log(f"Письмо отправлено, но исходное не помечено отвеченным: {e}")

    if not imap_smtp.saves_sent_copy(account.config):
        return sent

    try:
        sent.folder = await asyncio.to_thread(imap_smtp.append_sent, account.config, account.secret, message)

    except Exception as e:
        ctx.log(f"Письмо отправлено, но копия в «Отправленных» не сохранилась: {e}")
        sent.copy_error = str(e) or type(e).__name__

    return sent


@tool(
    name="mail.send",
    title="Отправить письмо",
    card="Отправить письмо {to} с темой «{subject}»",
    description=(
        "Send an email with attachments from a configured account; in_reply_to (a uid) makes it a reply in that "
        "letter's thread. In a task it saves a draft for the user to send instead, unless the task's setting "
        "send_directly says to send; a dry run always saves a draft. mode=unknown: the connection broke while "
        "sending — do not send it again."
    ),
    confirm="always",
    secrets=("account",),
    timeout=120,
    settings=Settings,
)
async def send(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)
    message = await compose(inp, ctx, account)
    answers = (inp.in_reply_to_folder, inp.in_reply_to) if inp.in_reply_to is not None else None

    return await deliver(ctx, account, message, draft=keeps_draft(ctx, ctx.dry_run), answers=answers)
