from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import Field, model_validator

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")
    uids: list[int] = Field(min_length=1, max_length=500, description="UID писем из mail.search, все из одной папки")
    folder: str = Field(default="INBOX", description="Папка, где письма лежат сейчас")
    seen: bool | None = Field(default=None, description="true — прочитано, false — непрочитано")
    flagged: bool | None = Field(default=None, description="true — отметить важным (звёздочка), false — снять")
    answered: bool | None = Field(default=None, description="true — отвечено, false — нет")
    move_to: str | None = Field(default=None, description="Переложить в эту папку (создаётся, если нет)")
    move_to_role: Literal["inbox", "archive", "junk", "trash"] | None = Field(
        default=None,
        description="Переложить в особую папку ящика: входящие, архив, спам, корзина (удалить письмо — trash); папку найдёт команда",
    )

    @model_validator(mode="after")
    def _something_to_do(self) -> Input:
        if self.move_to and self.move_to_role:
            raise ValueError("укажи одно: move_to или move_to_role")

        if not self.flags() and not self.move_to and not self.move_to_role:
            raise ValueError("укажи seen, flagged, answered, move_to или move_to_role")

        return self


    def flags(self) -> dict[str, bool]:
        return {name: value for name in imap_smtp.FLAGS if (value := getattr(self, name)) is not None}


class Output(ToolOutput):
    uids: list[int] = Field(description="Обработанные письма")
    missing: list[int] = Field(default_factory=list, description="Письма, которых в папке нет: их не тронули")
    folder: str = Field(description="Где письма теперь")


_SAID = {"seen": ("прочитано", "непрочитано"), "flagged": ("важное", "не важное"), "answered": ("отвечено", "не отвечено")}
_ROLES = {"inbox": "во входящие", "archive": "в архив", "junk": "в спам", "trash": "в корзину"}


@tool(
    name="mail.mark",
    title="Пометить письма",
    card="Пометить письма {uids} в «{account}»: прочитано — {seen}, важное — {flagged}, переложить в «{move_to}» {move_to_role}",
    description=(
        "Mark emails read/unread, flagged or answered and/or move them to a folder or to the mailbox's archive, junk, "
        "trash (to delete) or inbox. Use after handling messages so the next run does not handle them again (e.g. "
        "search unseen=true, then seen=true)."
    ),
    secrets=("account",),
)
async def mark(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)

    if ctx.dry_run:
        changes = [_SAID[name][0 if on else 1] for name, on in inp.flags().items()]
        changes += [f"переложить в «{inp.move_to}»"] if inp.move_to else []
        changes += [f"переложить {_ROLES[inp.move_to_role]}"] if inp.move_to_role else []
        ctx.log(f"Пробный запуск: письма {', '.join(map(str, inp.uids))} в «{inp.folder}» не тронуты; было бы: {', '.join(changes)}")
        return Output(uids=inp.uids, folder=inp.move_to or inp.folder)

    done = await asyncio.to_thread(
        imap_smtp.mark,
        account.config,
        account.secret,
        folder=inp.folder,
        uids=inp.uids,
        flags=inp.flags(),
        move_to=inp.move_to,
        move_to_role=inp.move_to_role,
    )

    if done["missing"]:
        ctx.log(f"Писем {', '.join(map(str, done['missing']))} в «{inp.folder}» нет: их не тронули")

    return Output(**done)
