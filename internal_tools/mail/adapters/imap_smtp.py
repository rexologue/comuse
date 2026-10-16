"""Blocking IMAP/SMTP helpers (stdlib only); tools call them via asyncio.to_thread."""

from __future__ import annotations

import imaplib
import mimetypes
import re
import smtplib
import ssl
from datetime import date
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Any

from common.tools import ToolError


_TIMEOUT_SEC = 30
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)')


def check(config: dict[str, Any], secret: str) -> None:
    with _imap(config, secret):
        pass

    with _smtp(config, secret):
        pass


def search(
    config: dict[str, Any],
    secret: str,
    *,
    folder: str,
    sender: str | None,
    subject: str | None,
    since: date | None,
    unseen: bool,
    limit: int,
    after_uid: int | None = None,
) -> list[dict[str, Any]]:
    criteria: list[str] = []

    if since:
        criteria += ["SINCE", since.strftime("%d-%b-%Y")]

    if unseen:
        criteria.append("UNSEEN")

    if sender and sender.isascii():
        criteria += ["FROM", f'"{sender}"']

    if after_uid is not None:
        criteria += ["UID", f"{after_uid + 1}:*"]

    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=True)

        status, data = imap.uid("SEARCH", *(criteria or ["ALL"]))
        if status != "OK":
            raise ToolError(f"Поиск в почте не удался: {data}")

        uids = [int(u) for u in data[0].split()]
        if after_uid is not None:
            uids = [u for u in uids if u > after_uid]

        found: list[dict[str, Any]] = []

        # Newest first; non-ASCII filters are applied client-side after decoding.
        for uid in reversed(uids):
            if len(found) >= limit:
                break

            status, parts = imap.uid("FETCH", str(uid), "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            if status != "OK" or not parts or parts[0] is None:
                continue

            headers = message_from_bytes(parts[0][1], policy=policy.default)
            item = {
                "uid": uid,
                "from": _header(headers, "From"),
                "to": _header(headers, "To"),
                "subject": _header(headers, "Subject"),
                "date": _iso_date(headers.get("Date")),
            }

            if subject and subject.lower() not in item["subject"].lower():
                continue

            if sender and not sender.isascii() and sender.lower() not in item["from"].lower():
                continue

            found.append(item)

    return found


def read(config: dict[str, Any], secret: str, *, folder: str, uid: int, save_dir: Path) -> dict[str, Any]:
    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=True)

        status, parts = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK" or not parts or parts[0] is None:
            raise ToolError(f"Письмо {uid} не найдено")

    message = message_from_bytes(parts[0][1], policy=policy.default)

    body = message.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

    attachments: list[Path] = []
    for part in message.iter_attachments():
        filename = part.get_filename()
        if not filename:
            continue

        save_dir.mkdir(parents=True, exist_ok=True)
        target = save_dir / Path(filename).name
        target.write_bytes(part.get_payload(decode=True) or b"")
        attachments.append(target)

    return {
        "uid": uid,
        "from": _header(message, "From"),
        "to": _header(message, "To"),
        "subject": _header(message, "Subject"),
        "date": _iso_date(message.get("Date")),
        "text": text,
        "attachments": attachments,
    }


def build_message(
    config: dict[str, Any],
    *,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[Path],
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = config["email"]
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid()
    message.set_content(body)

    for path in attachments:
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    return message


def send(config: dict[str, Any], secret: str, message: EmailMessage) -> None:
    with _smtp(config, secret) as smtp:
        smtp.send_message(message)


def append_draft(config: dict[str, Any], secret: str, message: EmailMessage) -> str:
    """Save into the provider's Drafts folder (the one flagged \\Drafts)."""
    with _imap(config, secret) as imap:
        folder = config.get("drafts_folder") or _drafts_folder(imap)

        status, data = imap.append(_quote(folder), r"(\Draft \Seen)", None, message.as_bytes())
        if status != "OK":
            raise ToolError(f"Не удалось сохранить черновик: {data}")

    return folder


class _imap:
    def __init__(self, config: dict[str, Any], secret: str):
        self.config = config
        self.secret = secret


    def __enter__(self) -> imaplib.IMAP4_SSL:
        try:
            self.conn = imaplib.IMAP4_SSL(
                self.config["imap_host"],
                int(self.config.get("imap_port", 993)),
                ssl_context=ssl.create_default_context(),
                timeout=_TIMEOUT_SEC,
            )
            self.conn.login(self.config.get("username") or self.config["email"], self.secret)

        except (imaplib.IMAP4.error, OSError) as e:
            raise ToolError(f"Не удалось войти в IMAP {self.config['imap_host']}: {e}") from e

        return self.conn


    def __exit__(self, *exc) -> None:
        try:
            self.conn.logout()

        except (imaplib.IMAP4.error, OSError):
            pass


class _smtp:
    def __init__(self, config: dict[str, Any], secret: str):
        self.config = config
        self.secret = secret


    def __enter__(self) -> smtplib.SMTP:
        host, port = self.config["smtp_host"], int(self.config.get("smtp_port", 465))
        context = ssl.create_default_context()

        try:
            if port == 465:
                self.conn = smtplib.SMTP_SSL(host, port, context=context, timeout=_TIMEOUT_SEC)
            else:
                self.conn = smtplib.SMTP(host, port, timeout=_TIMEOUT_SEC)
                self.conn.starttls(context=context)

            self.conn.login(self.config.get("username") or self.config["email"], self.secret)

        except (smtplib.SMTPException, OSError) as e:
            raise ToolError(f"Не удалось войти в SMTP {host}: {e}") from e

        return self.conn


    def __exit__(self, *exc) -> None:
        try:
            self.conn.quit()

        except (smtplib.SMTPException, OSError):
            pass


def _select(imap: imaplib.IMAP4_SSL, folder: str, *, readonly: bool) -> None:
    status, data = imap.select(_quote(folder), readonly=readonly)
    if status != "OK":
        raise ToolError(f"Папка почты «{folder}» не найдена: {data}")


def _drafts_folder(imap: imaplib.IMAP4_SSL) -> str:
    status, rows = imap.list()
    if status == "OK":
        for row in rows:
            match = _LIST_RE.match(row.decode(errors="replace"))
            if match and "\\Drafts" in match.group("flags"):
                return match.group("name").strip('"')

    return "Drafts"


def _quote(folder: str) -> str:
    return folder if folder.startswith('"') else f'"{folder}"'


def _header(message, name: str) -> str:
    value = message.get(name)
    if value is None:
        return ""

    try:
        return str(make_header(decode_header(str(value))))

    except (ValueError, LookupError):
        return str(value)


def _iso_date(value) -> str | None:
    if not value:
        return None

    try:
        return parsedate_to_datetime(str(value)).isoformat()

    except (TypeError, ValueError):
        return None
