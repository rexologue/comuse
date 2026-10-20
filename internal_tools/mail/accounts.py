"""The mail account kind: an IMAP/SMTP mailbox."""

from __future__ import annotations

from typing import Any

from common.tools import Credential, ToolContext, ToolError
from common.tools.accounts import AccountKind, account_kind
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.adapters.presets import PRESETS


KIND = "imap_smtp"


def _prepare(config: dict[str, Any]) -> dict[str, Any]:
    if not config.get("email"):
        raise ValueError("нужен адрес почты: config.email")

    missing = [key for key in ("imap_host", "smtp_host") if not config.get(key)]
    if missing:
        raise ValueError(f"не хватает настроек почты {missing}: укажите preset ({', '.join(PRESETS)}) или config")

    try:
        imap_smtp.connection_security(config, "imap")
        imap_smtp.connection_security(config, "smtp")

    except ToolError as e:
        raise ValueError(str(e)) from e

    return {**config, "username": config.get("username") or config["email"]}


account_kind(
    AccountKind(
        name=KIND,
        title="Почта IMAP/SMTP",
        presets=PRESETS,
        prepare=_prepare,
        check=imap_smtp.check,
    )
)


def mailbox(ctx: ToolContext) -> Credential:
    """The tool's `account` argument, which must be a mailbox."""
    credential = ctx.credential("account")

    if credential.kind != KIND:
        raise ToolError(f"Аккаунт `{credential.name}` не почтовый (вид {credential.kind})")

    return credential
