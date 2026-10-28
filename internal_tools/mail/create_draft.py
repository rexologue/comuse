from __future__ import annotations

import asyncio

from common.tools import ToolContext, tool
from internal_tools.mail.accounts import mailbox
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.send import Input, Output, compose


@tool(
    name="mail.create_draft",
    title="Создать черновик письма",
    card="Черновик письма {to} с темой «{subject}»",
    description="Save an email into the account's Drafts folder without sending it; in_reply_to (a uid) makes it a reply in that letter's thread.",
    secrets=("account",),
)
async def create_draft(inp: Input, ctx: ToolContext) -> Output:
    # The same with ctx.dry_run: a draft is exactly what a dry run of mail.send leaves, nothing goes out.
    account = mailbox(ctx)
    message = await compose(inp, ctx, account)

    folder = await asyncio.to_thread(imap_smtp.append_draft, account.config, account.secret, message)

    return Output(message_id=message["Message-ID"], mode="draft", folder=folder)
