from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolError, ToolInput, ToolOutput, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.addresses import AddressMask
from internal_tools.mail.labels import Label


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")
    folder: str = Field(default="INBOX", description="Папка почты")
    sender: str | None = Field(default=None, description="Отправитель содержит (подстрока имени или адреса; для удобства поиска, не для ограничений)")
    sender_in: list[AddressMask] = Field(
        default_factory=list,
        description=(
            "Только письма от этих адресов или доменов (*@company.ru), точное сравнение адреса отправителя. "
            "Обязательно, если найденным письмам задача отвечает или что-то с ними делает: укажи адреса или "
            "домен из разговора. Пусто — от кого угодно."
        ),
    )
    subject: str | None = Field(default=None, description="Тема содержит")
    since: str | None = Field(default=None, description="Не раньше даты YYYY-MM-DD (письма с этого дня и новее)")
    before: str | None = Field(
        default=None,
        description="Раньше даты YYYY-MM-DD, не включая её (письма старше): «старше 30 дней» — before = сегодня минус 30 дней",
    )
    unseen: bool = Field(default=False, description="Только непрочитанные")
    flagged: bool | None = Field(default=None, description="true — только важные (звёздочка), false — только не важные")
    answered: bool | None = Field(default=None, description="true — только отвеченные, false — только без ответа")
    labeled: list[Label] = Field(default_factory=list, description="Только письма со всеми этими метками")
    not_labeled: list[Label] = Field(
        default_factory=list,
        description="Только письма без этих меток: так задача берёт ещё не обработанные ею (её метку ставит mail.mark add_labels)",
    )
    limit: int = Field(default=20, ge=1, le=200)


class MailHeader(BaseModel):
    uid: int
    sender: str = Field(alias="from", description="Заголовок From целиком, с именем")
    sender_email: str = Field(alias="from_email", description="Только адрес отправителя — для ответа")
    to: str
    subject: str
    date: str | None
    flags: list[str] = Field(description="seen, answered, flagged, draft и метки письма")


class Output(ToolOutput):
    messages: list[MailHeader]


@tool(
    name="mail.search",
    title="Найти письма",
    card="Найти письма в «{account}»: отправитель «{sender}», только от {sender_in}, тема «{subject}»",
    description=(
        "Search an IMAP mailbox; newest first. Returns uid, sender, sender_email (bare address for replies), to, subject, "
        "date, flags. A task that handles new mail takes the letters without its label (not_labeled) and labels them after."
    ),
    confirm=False,
    secrets=("account",),
)
async def search(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)

    since, before = _date("since", inp.since), _date("before", inp.before)

    found = await asyncio.to_thread(
        imap_smtp.search,
        account.config,
        account.secret,
        folder=inp.folder,
        sender=inp.sender,
        subject=inp.subject,
        since=since,
        before=before,
        unseen=inp.unseen,
        limit=inp.limit,
        sender_in=inp.sender_in,
        flagged=inp.flagged,
        answered=inp.answered,
        labeled=inp.labeled,
        not_labeled=inp.not_labeled,
    )

    return Output(messages=[MailHeader.model_validate(item) for item in found])


def _date(name: str, value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None

    except ValueError as e:
        raise ToolError(f"`{name}` должен быть датой YYYY-MM-DD, получено {value!r}") from e
