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
    offset: int = Field(default=0, ge=0, description="С какого символа текста читать: для длинного письма — offset + max_chars прошлого вызова")
    max_chars: int = Field(default=20000, ge=1, le=100000, description="Сколько символов текста вернуть")


class Output(ToolOutput):
    uid: int
    sender: str
    sender_email: str
    to: str
    cc: str
    reply_to: str = Field(description="Куда просят отвечать, если не отправителю")
    subject: str
    date: str | None
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    flags: list[str] = Field(description="seen, answered, flagged, draft и метки сервера")
    text: str = Field(description="Текст письма с offset, не больше max_chars символов")
    text_length: int = Field(description="Длина всего текста письма")
    truncated: bool = Field(description="Текст продолжается дальше: прочитай с offset + max_chars")
    attachments: list[str]


@tool(
    name="mail.read",
    title="Прочитать письмо",
    card="Прочитать письмо {uid} из «{account}»",
    description=(
        "Read one email: text body (a window of max_chars from offset; truncated=true means there is more), "
        "thread headers and attachments (saved to data:outputs, paths returned)."
    ),
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

    text = data["text"]
    end = inp.offset + inp.max_chars

    return Output(
        uid=data["uid"],
        sender=data["from"],
        sender_email=data["from_email"],
        to=data["to"],
        cc=data["cc"],
        reply_to=data["reply_to"],
        subject=data["subject"],
        date=data["date"],
        message_id=data["message_id"],
        in_reply_to=data["in_reply_to"],
        references=data["references"],
        flags=data["flags"],
        text=text[inp.offset : end],
        text_length=len(text),
        truncated=end < len(text),
        attachments=attachments,
    )
