from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.adapters import imap_smtp


class Input(ToolInput):
    account: str = Field(description="Имя почтового аккаунта")


class MailFolder(BaseModel):
    name: str = Field(description="Имя папки, как его ждут folder и move_to")
    role: str | None = Field(description="inbox, sent, drafts, archive, junk, trash — или пусто для обычной папки")


class Output(ToolOutput):
    folders: list[MailFolder]


@tool(
    name="mail.folders",
    title="Папки почты",
    card="Папки почты «{account}»",
    description="List the mailbox's folders with the role of the special ones (inbox, sent, drafts, archive, junk, trash).",
    confirm=False,
    secrets=("account",),
)
async def folders(inp: Input, ctx: ToolContext) -> Output:
    account = mailbox(ctx)

    found = await asyncio.to_thread(imap_smtp.folder_list, account.config, account.secret)

    return Output(folders=[MailFolder.model_validate(item) for item in found])
