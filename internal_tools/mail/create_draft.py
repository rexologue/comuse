from __future__ import annotations

import asyncio

from common.tools import ToolContext, tool
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.send import Input, Output


@tool(
    name="mail.create_draft",
    title="Создать черновик письма",
    card="Черновик письма {to} с темой «{subject}»",
    description="Save an email into the account's Drafts folder without sending it.",
    side_effect="write",
    secrets=("account",),
    dry_run="skip",
)
async def create_draft(inp: Input, ctx: ToolContext) -> Output:
    account = ctx.credential("account")

    attachments = [ctx.read_path(ref) for ref in inp.attachments]
    message = imap_smtp.build_message(
        account.config,
        to=list(inp.to),
        cc=list(inp.cc),
        subject=inp.subject,
        body=inp.body,
        attachments=attachments,
    )

    folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)

    return Output(message_id=message["Message-ID"], mode="draft", folder=folder)
