"""The mail account kind: an IMAP/SMTP mailbox."""

from __future__ import annotations

from typing import Any

from common.tools import Credential, ToolContext, ToolError
from common.tools.accounts import AccountField, AccountKind, account_kind
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.adapters.presets import PRESETS
from internal_tools.mail.addresses import check_mask, matches


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

    # Kept as the person wrote it; a mask that is not one is refused now, not at a send.
    for mask in allowed_recipients(config):
        check_mask(mask)

    # No username stored for the address: the login falls back to it, so editing the
    # address later does not leave a stale login behind.
    return config


account_kind(
    AccountKind(
        name=KIND,
        title="Почта IMAP/SMTP",
        fields=(
            AccountField("email", "Адрес почты", required=True),
            AccountField("username", "Логин", hint="если не совпадает с адресом", default="адрес почты"),
            AccountField("imap_host", "Сервер IMAP", hint="заполняется пресетом"),
            AccountField("imap_port", "Порт IMAP", type="number", default="993 при ssl, иначе 143"),
            AccountField("imap_security", "Защита IMAP", type="choice", choices=("ssl", "starttls", "none"), default="ssl"),
            AccountField(
                "security", "Защита IMAP и SMTP сразу", type="choice", choices=("ssl", "starttls", "none"),
                hint="если у обоих одна; своя у каждого — imap_security, smtp_security",
            ),
            AccountField("smtp_host", "Сервер SMTP", hint="заполняется пресетом"),
            AccountField("smtp_port", "Порт SMTP", type="number", default="465 при ssl, 587 при starttls, 25 без защиты"),
            AccountField(
                "smtp_security", "Защита SMTP", type="choice", choices=("ssl", "starttls", "none"), default="ssl на порту 465, иначе starttls"
            ),
            AccountField("drafts_folder", "Папка черновиков", hint="если не находится сама"),
            AccountField("sent_folder", "Папка отправленных", hint="если не находится сама"),
            AccountField(
                "allowed_recipients",
                "Кому можно писать",
                hint="адреса и домены через запятую: boss@company.ru, *@company.ru; пусто — кому угодно",
            ),
            AccountField(
                "save_sent",
                "Копия в отправленных",
                type="choice",
                choices=("yes", "no"),
                hint="no — если сервер сам сохраняет отправленное (Gmail, Outlook)",
                default="yes",
            ),
        ),
        secret_title="Пароль приложения",
        presets=PRESETS,
        prepare=_prepare,
        check=imap_smtp.check,
    )
)


def allowed_recipients(config: dict[str, Any]) -> list[str]:
    return [mask.strip().lower() for mask in str(config.get("allowed_recipients") or "").split(",") if mask.strip()]


def check_recipients(account: Credential, *addresses: list[str]) -> None:
    """A mailbox that may write only to some (`allowed_recipients`): a letter to anyone
    else is not made at all — sent, forwarded or left in Drafts. What a letter the model
    read asked it to do cannot widen this: the person sets it on the account."""
    masks = allowed_recipients(account.config)
    if not masks:
        return

    refused = [address for group in addresses for address in group if not matches(address, masks)]
    if refused:
        raise ToolError(f"С ящика «{account.name}» можно писать только {', '.join(masks)}; не разрешены: {', '.join(refused)}")


def mailbox(ctx: ToolContext) -> Credential:
    """The tool's `account` argument, which must be a mailbox."""
    credential = ctx.credential("account")

    if credential.kind != KIND:
        raise ToolError(f"Аккаунт `{credential.name}` не почтовый (вид {credential.kind})")

    return credential
