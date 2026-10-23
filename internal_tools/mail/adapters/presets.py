"""Server settings for common providers; all of them need an app password, not the
account's main one."""

from __future__ import annotations


PRESETS: dict[str, dict[str, object]] = {
    "gmail": {
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        # Gmail keeps what goes through its SMTP in Sent itself.
        "save_sent": "no",
    },
    "yandex": {
        "imap_host": "imap.yandex.ru",
        "imap_port": 993,
        "smtp_host": "smtp.yandex.ru",
        "smtp_port": 465,
    },
    "mailru": {
        "imap_host": "imap.mail.ru",
        "imap_port": 993,
        "smtp_host": "smtp.mail.ru",
        "smtp_port": 465,
    },
    "outlook": {
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        # So does Exchange Online.
        "save_sent": "no",
    },
}
