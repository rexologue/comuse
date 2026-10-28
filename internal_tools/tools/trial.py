from __future__ import annotations

from typing import Any

from pydantic import Field

from common.tools import ToolContext, ToolInput, ToolOutput, tool
from executor.plugins.launch import try_draft
from internal_tools.tools.common import chat_only, split


class Input(ToolInput):
    name: str = Field(description="Команда черновика: группа.действие, например telegram.send")
    args: dict[str, Any] = Field(default_factory=dict, description="Аргументы, как их получит run(args, ctx)")
    account: str | None = Field(
        default=None,
        description="Аккаунт, от имени которого работает плагин (если работает): его настройки и пароль получит черновик",
    )


class Output(ToolOutput):
    output: Any


@tool(
    name="tools.try",
    title="Попробовать свою команду",
    card="Попробовать «{name}» с аргументами {args}; аккаунт «{account}» — черновик получит его пароль",
    description=(
        "Run a command of a plugin's draft once, on real arguments, to see it work before "
        "the user approves it. Installs its packages on first use. A plugin that works for an account "
        "is tried with `account`, and its unapproved code gets that account's secret. Asks the user every "
        "time: the code is not approved yet."
    ),
    confirm="always",
    secrets=("account",),
    chat_only=True,
)
async def trial(inp: Input, ctx: ToolContext) -> Output:
    # chat_only refuses a task and ctx.dry_run: an unapproved plugin runs only on a yes.
    chat_only(ctx)
    group, action = split(inp.name)

    args = {**inp.args, "account": inp.account} if inp.account else inp.args

    return Output(output=await try_draft(ctx.settings.storage, group, action, args, ctx))
