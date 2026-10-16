from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")
    folder: str = Field(default="INBOX", description="Папка почты")
    sender: str | None = Field(default=None, description="Отправитель содержит")
    subject: str | None = Field(default=None, description="Тема содержит")
    since: str | None = Field(default=None, description="Не раньше даты YYYY-MM-DD")
    unseen: bool = Field(default=False, description="Только непрочитанные")
    limit: int = Field(default=20, ge=1, le=200)


class MailHeader(BaseModel):
    uid: int
    sender: str = Field(alias="from")
    to: str
    subject: str
    date: str | None


class Output(ToolOutput):
    messages: list[MailHeader]


@tool(
    name="mail.search",
    title="Найти письма",
    card="Найти письма в «{account}»: отправитель «{sender}», тема «{subject}»",
    description="Search an IMAP mailbox; newest first. Returns uid, from, to, subject, date.",
    side_effect="read",
    capabilities=("mail.account:{account}",),
    secrets=("account",),
)
async def search(inp: Input, ctx: ToolContext) -> Output:
    account = ctx.account(inp.account)

    try:
        since = date.fromisoformat(inp.since[:10]) if inp.since else None

    except ValueError as e:
        raise ToolError(f"`since` должен быть датой YYYY-MM-DD, получено {inp.since!r}") from e

    found = await asyncio.to_thread(
        imap_smtp.search,
        account.config,
        account.secret,
        folder=inp.folder,
        sender=inp.sender,
        subject=inp.subject,
        since=since,
        unseen=inp.unseen,
        limit=inp.limit,
    )

    return Output(messages=[MailHeader.model_validate(item) for item in found])
