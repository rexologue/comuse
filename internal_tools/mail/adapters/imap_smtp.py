"""Blocking IMAP/SMTP helpers (stdlib only); tools call them via asyncio.to_thread."""

from __future__ import annotations

import base64
import imaplib
import mimetypes
import re
import smtplib
import ssl
from datetime import date
from email import message_from_bytes, policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from common.tools import ToolError
from internal_tools.mail.addresses import matches as address_matches


_TIMEOUT_SEC = 30

_SECURITY = ("ssl", "starttls", "none")

_DEFAULT_PORTS = {
    "imap": {"ssl": 993, "starttls": 143, "none": 143},
    "smtp": {"ssl": 465, "starttls": 587, "none": 25},
}
_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) "(?P<sep>[^"]*)" (?P<name>.+)')
_UTF7_RE = re.compile(r"&([^-]*)-")
_FETCH_UID_RE = re.compile(rb"\bUID (\d+)")

# Message headers fetched per IMAP round trip while searching.
_HEADERS_BATCH = 50


def connection_security(config: dict[str, Any], protocol: str) -> str:
    """`imap_security` / `smtp_security`, else the shared `security`, else what the
    port implies (IMAP: SSL; SMTP: SSL on 465, STARTTLS otherwise) — the behaviour
    before these settings existed, so old accounts keep working."""
    value = config.get(f"{protocol}_security") or config.get("security")

    if value is None:
        if protocol == "smtp" and int(config.get("smtp_port") or 465) != 465:
            value = "starttls"
        else:
            value = "ssl"

    if value not in _SECURITY:
        raise ToolError(f"`{protocol}_security` должен быть одним из {list(_SECURITY)}, получено {value!r}")

    return value


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
    sender_in: list[str] = (),
) -> list[dict[str, Any]]:
    # A message flagged \\Deleted is gone for the user even before the server purges it.
    criteria: list[str] = ["UNDELETED"]

    if since:
        criteria += ["SINCE", since.strftime("%d-%b-%Y")]

    if unseen:
        criteria.append("UNSEEN")

    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=True)

        status, data = imap.uid("SEARCH", *criteria)
        if status != "OK":
            raise ToolError(f"Поиск в почте не удался: {data}")

        newest_first = [int(u) for u in reversed(data[0].split())]
        found: list[dict[str, Any]] = []

        # Sender and subject are matched here, on decoded headers: servers disagree on
        # FROM/SUBJECT search (substring or not, non-ASCII or not). Headers come in
        # batches: one round trip per message is minutes on a big mailbox.
        for start in range(0, len(newest_first), _HEADERS_BATCH):
            batch = newest_first[start : start + _HEADERS_BATCH]
            headers = _fetch_headers(imap, batch)

            for uid in batch:
                if uid not in headers:
                    continue

                item = _header_item(uid, headers[uid])
                if matches(item, sender=sender, subject=subject, sender_in=sender_in):
                    found.append(item)

                if len(found) >= limit:
                    return found

    return found


def _fetch_headers(imap: imaplib.IMAP4, uids: list[int]) -> dict[int, bytes]:
    status, parts = imap.uid("FETCH", ",".join(map(str, uids)), "(UID BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
    if status != "OK":
        return {}

    headers: dict[int, bytes] = {}
    for part in parts or []:
        if isinstance(part, tuple) and (match := _FETCH_UID_RE.search(part[0])):
            headers[int(match.group(1))] = part[1]

    return headers


def _header_item(uid: int, raw: bytes) -> dict[str, Any]:
    headers = message_from_bytes(raw, policy=policy.default)

    return {
        "uid": uid,
        "from": _header(headers, "From"),
        "from_email": address_of(_header(headers, "From")),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _iso_date(headers.get("Date")),
    }


def matches(item: dict[str, Any], *, sender: str | None, subject: str | None, sender_in: list[str] = ()) -> bool:
    """`sender` and `subject` are case-insensitive substrings (the sender of the whole
    From header: a name, an address or a domain); `sender_in` compares the bare address
    exactly against addresses and `*@domain` masks."""
    if subject and subject.lower() not in item["subject"].lower():
        return False

    if sender and sender.lower() not in item["from"].lower():
        return False

    if sender_in and not address_matches(item["from_email"], sender_in):
        return False

    return True


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
    for n, part in enumerate(message.iter_attachments(), start=1):
        if not part.get_filename():
            continue

        save_dir.mkdir(parents=True, exist_ok=True)
        target = _free_name(save_dir, _attachment_name(part.get_filename(), n))
        target.write_bytes(part.get_payload(decode=True) or b"")
        attachments.append(target)

    return {
        "uid": uid,
        "from": _header(message, "From"),
        "from_email": address_of(_header(message, "From")),
        "to": _header(message, "To"),
        "subject": _header(message, "Subject"),
        "date": _iso_date(message.get("Date")),
        "text": text,
        "attachments": attachments,
    }


def mark(config: dict[str, Any], secret: str, *, folder: str, uid: int, seen: bool | None, move_to: str | None) -> str:
    """Set or clear \\Seen and/or move the message; returns the folder it ends up in."""
    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=False)

        if seen is not None:
            status, data = imap.uid("STORE", str(uid), "+FLAGS" if seen else "-FLAGS", r"(\Seen)")
            if status != "OK" or not data or data[0] is None:
                raise ToolError(f"Письмо {uid} в «{folder}» не найдено")

        if not move_to or move_to == folder:
            return folder

        _ensure_folder(imap, move_to)

        if "MOVE" in imap.capabilities:  # RFC 6851
            status, data = imap.uid("MOVE", str(uid), _quote(move_to))
            if status != "OK":
                raise ToolError(f"Не удалось переложить письмо {uid} в «{move_to}»: {data}")

            return move_to

        status, data = imap.uid("COPY", str(uid), _quote(move_to))
        if status != "OK":
            raise ToolError(f"Не удалось переложить письмо {uid} в «{move_to}»: {data}")

        imap.uid("STORE", str(uid), "+FLAGS", r"(\Deleted)")

        # A plain EXPUNGE would also purge everything else the user flagged \Deleted;
        # without UIDPLUS (RFC 4315) the original just stays flagged, which search skips.
        if "UIDPLUS" in imap.capabilities:
            imap.uid("EXPUNGE", str(uid))

    return move_to


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
        folder = config.get("drafts_folder") or _special_folder(imap, "\\Drafts", "Drafts", "черновиков")

        status, data = imap.append(_quote(folder), r"(\Draft \Seen)", None, message.as_bytes())
        if status != "OK":
            raise ToolError(f"Не удалось сохранить черновик: {data}")

    return folder


def saves_sent_copy(config: dict[str, Any]) -> bool:
    """Whether a sent letter needs a copy in Sent from us: SMTP only delivers it, and the
    mail client keeps the copy. `save_sent: no` for servers that keep one themselves
    (Gmail, Outlook), or every letter would be there twice."""
    return config.get("save_sent") != "no"


def append_sent(config: dict[str, Any], secret: str, message: EmailMessage) -> str:
    """Keep a sent letter in the provider's Sent folder (the one flagged \\Sent)."""
    with _imap(config, secret) as imap:
        folder = config.get("sent_folder") or _special_folder(imap, "\\Sent", "Sent", "отправленных")

        status, data = imap.append(_quote(folder), r"(\Seen)", None, message.as_bytes())
        if status != "OK":
            raise ToolError(f"Не удалось сохранить письмо в «{folder}»: {data}")

    return folder


class _imap:
    def __init__(self, config: dict[str, Any], secret: str):
        self.config = config
        self.secret = secret


    def __enter__(self) -> imaplib.IMAP4:
        host = self.config["imap_host"]
        security = connection_security(self.config, "imap")
        port = int(self.config.get("imap_port") or _DEFAULT_PORTS["imap"][security])

        try:
            if security == "ssl":
                self.conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=_TIMEOUT_SEC)
            else:
                self.conn = imaplib.IMAP4(host, port, timeout=_TIMEOUT_SEC)
                if security == "starttls":
                    self.conn.starttls(ssl_context=ssl.create_default_context())

            self.conn.login(self.config.get("username") or self.config["email"], self.secret)

        except (imaplib.IMAP4.error, OSError) as e:
            raise ToolError(f"Не удалось войти в IMAP {host}:{port} ({security}): {e}") from e

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
        host = self.config["smtp_host"]
        security = connection_security(self.config, "smtp")
        port = int(self.config.get("smtp_port") or _DEFAULT_PORTS["smtp"][security])
        context = ssl.create_default_context()

        try:
            if security == "ssl":
                self.conn = smtplib.SMTP_SSL(host, port, context=context, timeout=_TIMEOUT_SEC)
            else:
                self.conn = smtplib.SMTP(host, port, timeout=_TIMEOUT_SEC)
                if security == "starttls":
                    self.conn.starttls(context=context)

            # A relay that does not offer AUTH takes mail without a login.
            self.conn.ehlo_or_helo_if_needed()
            if self.conn.has_extn("auth"):
                self.conn.login(self.config.get("username") or self.config["email"], self.secret)

        except (smtplib.SMTPException, OSError) as e:
            raise ToolError(f"Не удалось войти в SMTP {host}:{port} ({security}): {e}") from e

        return self.conn


    def __exit__(self, *exc) -> None:
        try:
            self.conn.quit()

        except (smtplib.SMTPException, OSError):
            pass


def _select(imap: imaplib.IMAP4, folder: str, *, readonly: bool) -> None:
    status, data = imap.select(_quote(folder), readonly=readonly)
    if status != "OK":
        raise ToolError(f"Папка почты «{folder}» не найдена: {data}")


def _special_folder(imap: imaplib.IMAP4, flag: str, fallback: str, what: str) -> str:
    """The folder flagged `flag` (RFC 6154: \\Drafts, \\Sent), else one named `fallback`
    — created if missing (some servers start with INBOX only)."""
    names: list[str] = []

    status, rows = imap.list()
    if status == "OK":
        for row in rows or []:
            if row is None:
                continue

            match = _LIST_RE.match(row.decode(errors="replace"))
            if not match:
                continue

            name = decode_folder(_unquote(match.group("name")))
            if flag in match.group("flags"):
                return name

            names.append(name)

    if fallback not in names:
        status, data = imap.create(_quote(fallback))
        if status != "OK":
            raise ToolError(f"Нет папки {what} и не удалось её создать: {data}")

    return fallback


def _ensure_folder(imap: imaplib.IMAP4, folder: str) -> None:
    status, rows = imap.list('""', _quote(folder))
    if status == "OK" and any(rows or []):
        return

    status, data = imap.create(_quote(folder))
    if status != "OK":
        raise ToolError(f"Не удалось создать папку почты «{folder}»: {data}")


def _quote(folder: str) -> str:
    """A folder as one IMAP quoted string, whatever the name: it may come from a letter
    the model read, so nothing in it can end the string or the command."""
    encoded = encode_folder(folder).replace("\\", "\\\\").replace('"', '\\"')

    return f'"{encoded}"'


def _unquote(name: str) -> str:
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return re.sub(r'\\(.)', r"\1", name[1:-1])

    return name


def encode_folder(name: str) -> str:
    """IMAP folder names are modified UTF-7 (RFC 3501 5.1.3): `Входящие` → `&BBIERQQ+BDQETwRJBDgENQ-`."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            raw = base64.b64encode("".join(pending).encode("utf-16-be")).decode()
            out.append("&" + raw.rstrip("=").replace("/", ",") + "-")
            pending.clear()

    for char in name:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            pending.append(char)

    flush()
    return "".join(out)


def decode_folder(name: str) -> str:
    def decode(match: re.Match) -> str:
        chunk = match.group(1)
        if not chunk:
            return "&"

        raw = chunk.replace(",", "/")
        return base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-16-be")

    return _UTF7_RE.sub(decode, name)


def _attachment_name(filename: str, n: int) -> str:
    """The sender names the file: only its last part, nothing Windows refuses, and never
    `..` or empty."""
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")

    return name or f"attachment_{n}"


def _free_name(folder: Path, name: str) -> Path:
    """Two attachments called image.png: the second becomes image (2).png."""
    target, stem, suffix = folder / name, Path(name).stem, Path(name).suffix

    for copy in range(2, 1000):
        if not target.exists():
            break
        target = folder / f"{stem} ({copy}){suffix}"

    return target


def address_of(header: str) -> str:
    """`Иван Петров <ivan@company.local>` → `ivan@company.local`; what a reply needs."""
    return parseaddr(header)[1].strip()


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
