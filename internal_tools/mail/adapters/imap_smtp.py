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
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any

from common.tools import ToolError
from internal_tools.mail.adapters import folders, letter
from internal_tools.mail.addresses import matches as address_matches


_TIMEOUT_SEC = 30

_SECURITY = ("ssl", "starttls", "none")

_DEFAULT_PORTS = {
    "imap": {"ssl": 993, "starttls": 143, "none": 143},
    "smtp": {"ssl": 465, "starttls": 587, "none": 25},
}
_FETCH_UID_RE = re.compile(rb"\bUID (\d+)")
_SIZE_RE = re.compile(rb"\bRFC822\.SIZE (\d+)")
_FLAGS_RE = re.compile(rb"\bFLAGS \(([^)]*)\)")
_MSGID_RE = re.compile(r"<[^<>\s]+>")
# A subject that is a reply already (Re:, RE[2]:, Ответ:, AW: …), a forward already (Fwd:, FW:, Пересл:).
_RE_RE = re.compile(r"^\s*(re|ответ|aw|sv)\s*(\[\d+\])?\s*:", re.IGNORECASE)
_FWD_RE = re.compile(r"^\s*(fwd?|пересл)\s*(\[\d+\])?\s*:", re.IGNORECASE)

# A bigger letter is not read: its text and files are more than a step should take in.
MAX_LETTER_BYTES = 50 * 2**20

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
    before: date | None = None,
    sender_in: list[str] = (),
    flagged: bool | None = None,
    answered: bool | None = None,
    labeled: list[str] = (),
    not_labeled: list[str] = (),
) -> list[dict[str, Any]]:
    """Flags and labels are the server's search (KEYWORD, UNKEYWORD): atoms every server
    compares the same; sender and subject are ours (see below)."""
    # A message flagged \\Deleted is gone for the user even before the server purges it.
    criteria: list[str] = ["UNDELETED"]

    # The date the server got the letter (INTERNALDATE), which every letter has: Date is
    # the sender's word and may be missing.
    if since:
        criteria += ["SINCE", _imap_date(since)]

    if before:
        criteria += ["BEFORE", _imap_date(before)]

    if unseen:
        criteria.append("UNSEEN")

    if flagged is not None:
        criteria.append("FLAGGED" if flagged else "UNFLAGGED")

    if answered is not None:
        criteria.append("ANSWERED" if answered else "UNANSWERED")

    criteria += [word for label in labeled for word in ("KEYWORD", label)]
    criteria += [word for label in not_labeled for word in ("UNKEYWORD", label)]

    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=True)

        status, data = imap.uid("SEARCH", *criteria)
        if status != "OK":
            raise ToolError(f"Поиск в почте не удался: {folders.said(data)}")

        # Nothing found: iCloud sends no `* SEARCH` line at all, imaplib gives [None].
        newest_first = [int(u) for u in reversed(((data or [None])[0] or b"").split())]
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

                item = _header_item(uid, *headers[uid])
                if matches(item, sender=sender, subject=subject, sender_in=sender_in):
                    found.append(item)

                if len(found) >= limit:
                    return found

    return found


def _imap_date(day: date) -> str:
    """01-Nov-2026: English month names, as IMAP has them, whatever the machine's locale (%b may be «ноя»)."""
    return f"{day.day:02d}-{('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[day.month - 1]}-{day.year}"


def _fetch_headers(imap: imaplib.IMAP4, uids: list[int], fields: str = "FROM TO SUBJECT DATE") -> dict[int, tuple[bytes, list[str]]]:
    """uid → (its headers, its flags)."""
    status, parts = imap.uid("FETCH", ",".join(map(str, uids)), f"(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS ({fields})])")
    if status != "OK":
        return {}

    # Some servers put the UID (and the flags) after the headers: imaplib gives them as
    # the next item, `b' UID 5 FLAGS (\\Seen))'`.
    parts = list(parts or [])
    headers: dict[int, tuple[bytes, list[str]]] = {}

    for n, part in enumerate(parts):
        if not isinstance(part, tuple):
            continue

        after = parts[n + 1] if n + 1 < len(parts) and isinstance(parts[n + 1], bytes) else b""
        if match := _FETCH_UID_RE.search(part[0]) or _FETCH_UID_RE.search(after):
            info = part[0] + b" " + after
            headers[int(match.group(1))] = (part[1], _flags(_FLAGS_RE.search(info)), _received(info))

    return headers


def _header_item(uid: int, raw: bytes, flags: list[str], received: str | None = None) -> dict[str, Any]:
    headers = message_from_bytes(raw, policy=policy.default)

    return {
        "uid": uid,
        "flags": flags,
        "from": _header(headers, "From"),
        "from_email": address_of(_header(headers, "From")),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject"),
        "date": _iso_date(headers.get("Date")) or received,
    }


def _received(info: bytes) -> str | None:
    """When the server got the letter (INTERNALDATE): every letter has it, Date is the sender's and may be missing."""
    stamp = imaplib.Internaldate2tuple(info) if b"INTERNALDATE" in info else None
    if stamp is None:
        return None

    from datetime import datetime

    return datetime(*stamp[:6]).astimezone().isoformat()


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


def _fetch_letter(imap: imaplib.IMAP4, folder: str, uid: int) -> tuple[EmailMessage, bytes]:
    """The whole letter and what the server says of it (its flags, its size)."""
    _select(imap, folder, readonly=True)

    # Its size first: a letter of hundreds of megabytes is not downloaded to be refused.
    status, parts = imap.uid("FETCH", str(uid), "(FLAGS RFC822.SIZE INTERNALDATE)")
    info = b" ".join(part[0] if isinstance(part, tuple) else part for part in parts or [] if part) if status == "OK" else b""
    size = _SIZE_RE.search(info)
    if size is None:
        raise ToolError(f"Письмо {uid} в «{folder}» не найдено")

    if int(size.group(1)) > MAX_LETTER_BYTES:
        raise ToolError(f"Письмо {uid} весит {int(size.group(1)) // 2**20} МБ — больше {MAX_LETTER_BYTES // 2**20} МБ, его не скачать")

    status, parts = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
    # The letter is the literal after BODY[]: an unsolicited FETCH (new flags) may come first.
    raw = next((part[1] for part in parts or [] if isinstance(part, tuple) and b"BODY[]" in part[0]), None) if status == "OK" else None
    if raw is None:
        raise ToolError(f"Письмо {uid} в «{folder}» не найдено")

    return message_from_bytes(raw, policy=policy.default), info


def read(config: dict[str, Any], secret: str, *, folder: str, uid: int, save_dir: Path, sender_in: list[str] = ()) -> dict[str, Any]:
    """`sender_in`: only a letter from these addresses or domains is read; another one
    is refused before anything of it (its text, its files) is given or saved."""
    with _imap(config, secret) as imap:
        message, info = _fetch_letter(imap, folder, uid)

    if sender_in and not address_matches(address_of(_header(message, "From")), sender_in):
        raise ToolError(f"Письмо {uid} в «{folder}» не от {', '.join(sender_in)}: его не читаем")

    text, files = letter.text_and_files(message)

    attachments = [_save_file(save_dir, _attachment_name(file.filename or _unnamed(file, n), n), file.content) for n, file in enumerate(files, start=1)]

    return {
        "uid": uid,
        "from": _header(message, "From"),
        "from_email": address_of(_header(message, "From")),
        "to": _header(message, "To"),
        "cc": _header(message, "Cc"),
        "reply_to": _header(message, "Reply-To"),
        "subject": _header(message, "Subject"),
        "date": _iso_date(message.get("Date")) or _received(info),
        "message_id": _header(message, "Message-ID").strip() or None,
        "in_reply_to": _header(message, "In-Reply-To").strip() or None,
        "references": _MSGID_RE.findall(_header(message, "References")),
        "flags": _flags(_FLAGS_RE.search(info)),
        "text": text,
        "attachments": attachments,
    }


def thread_of(config: dict[str, Any], secret: str, *, folder: str, uid: int) -> dict[str, Any]:
    """What a reply to the letter takes from it: its subject and its place in the thread."""
    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=True)
        found = _fetch_headers(imap, [uid], "SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES").get(uid)

    if found is None:
        raise ToolError(f"Письмо {uid} в «{folder}» не найдено")

    headers = message_from_bytes(found[0], policy=policy.default)

    return {
        "subject": _header(headers, "Subject"),
        "message_id": (_MSGID_RE.findall(_header(headers, "Message-ID")) or [None])[0],
        "in_reply_to": (_MSGID_RE.findall(_header(headers, "In-Reply-To")) or [None])[0],
        "references": _MSGID_RE.findall(_header(headers, "References")),
    }


def forward_message(
    config: dict[str, Any],
    secret: str,
    *,
    folder: str,
    uid: int,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    note: str,
    attachments: bool,
) -> EmailMessage:
    """A new letter that carries the source's text and files under the note. Only what
    we write goes in: none of the source's own headers (Received, its Bcc, its DKIM)."""
    with _imap(config, secret) as imap:
        source, _ = _fetch_letter(imap, folder, uid)

    text, files = letter.text_and_files(source)

    fields = {"От": "From", "Дата": "Date", "Тема": "Subject", "Кому": "To", "Копия": "Cc"}
    quoted = ["---------- Пересылаемое письмо ----------"]
    quoted += [f"{label}: {_header(source, name)}" for label, name in fields.items() if _header(source, name)]

    body = "\n\n".join(part for part in (note.strip(), "\n".join(quoted), text) if part)
    subject = _header(source, "Subject")

    message = build_message(
        config,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject if _FWD_RE.match(subject) else f"Fwd: {subject}",
        body=body,
        attachments=[],
    )

    for n, file in enumerate(files if attachments else [], start=1):
        name = _attachment_name(file.filename or _unnamed(file, n), n)
        if file.content_type == "message/rfc822":
            message.add_attachment(message_from_bytes(file.content, policy=policy.default), filename=name)
        else:
            maintype, _, subtype = file.content_type.partition("/")
            message.add_attachment(file.content, maintype=maintype, subtype=subtype or "octet-stream", filename=name)

    return message


def set_flag(config: dict[str, Any], secret: str, *, folder: str, uid: int, flag: str) -> None:
    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=False)

        status, data = imap.uid("STORE", str(uid), "+FLAGS", f"({flag})")
        if status != "OK" or not data or data[0] is None:
            raise ToolError(f"Письмо {uid} в «{folder}» не найдено")


def _flags(match: re.Match | None) -> list[str]:
    """`\\Seen \\Answered $Label` → ["seen", "answered", "$Label"]; \\Recent is the server's, not the letter's."""
    flags = match.group(1).decode(errors="replace").split() if match else []

    return [flag[1:].lower() if flag.startswith("\\") else flag for flag in flags if flag.lower() != "\\recent"]


# What mail.mark sets: our name → the IMAP flag.
FLAGS = {"seen": r"\Seen", "flagged": r"\Flagged", "answered": r"\Answered"}


def mark(
    config: dict[str, Any],
    secret: str,
    *,
    folder: str,
    uids: list[int],
    flags: dict[str, bool],
    move_to: str | None = None,
    move_to_role: str | None = None,
    add_labels: list[str] = (),
    remove_labels: list[str] = (),
) -> dict[str, Any]:
    """Set or clear `flags` (FLAGS names → on/off) and labels (IMAP keywords), and/or move
    the letters to a folder or to the folder of a role (archive, junk, trash, inbox).
    The letters that are not in `folder` are named, the others done; none there at all
    is an error."""
    with _imap(config, secret) as imap:
        _select(imap, folder, readonly=False)

        # MOVE and COPY of a UID that is not there answer OK and do nothing.
        status, data = imap.uid("SEARCH", "UID", ",".join(map(str, uids)))
        there = {int(uid) for uid in ((data or [None])[0] or b"").split()} if status == "OK" else set()
        found = [uid for uid in uids if uid in there]
        missing = [uid for uid in uids if uid not in there]
        if not found:
            raise ToolError(f"Писем {', '.join(map(str, uids))} в «{folder}» нет")

        uid_set = ",".join(map(str, found))

        for name, on in flags.items():
            _store(imap, uid_set, "+FLAGS" if on else "-FLAGS", FLAGS[name], folder)

        if add_labels:
            _check_labels(imap, folder, add_labels)
            _store(imap, uid_set, "+FLAGS", " ".join(add_labels), folder)

        if remove_labels:
            _store(imap, uid_set, "-FLAGS", " ".join(remove_labels), folder)

        target = move_to
        if move_to_role == "inbox":
            target = "INBOX"
        elif move_to_role is not None:
            target = folders.special_folder(imap, move_to_role)
        elif move_to:
            target = folders.on_server(imap, move_to)
            folders.ensure_folder(imap, target)

        if target and target != folder:
            _move(imap, uid_set, target)

    return {"uids": found, "missing": missing, "folder": target or folder}


def _check_labels(imap: imaplib.IMAP4, folder: str, labels: list[str]) -> None:
    """A server that keeps no labels of ours says so in PERMANENTFLAGS (no `\\*`) and may
    still answer OK to STORE: the label would be gone at the next login, and a task that
    skips labelled letters would handle them again and again."""
    permanent = b" ".join(item for item in (getattr(imap, "untagged_responses", {}) or {}).get("PERMANENTFLAGS", []) if isinstance(item, bytes))
    if not permanent:
        return

    kept = {flag.lower() for flag in permanent.decode(errors="replace").strip("()").split()}
    if "\\*" not in kept and (lost := [label for label in labels if label.lower() not in kept]):
        raise ToolError(f"Сервер не хранит свои метки писем в «{folder}»: «{', '.join(lost)}» не сохранится")


def _store(imap: imaplib.IMAP4, uid_set: str, how: str, flag: str, folder: str) -> None:
    status, data = imap.uid("STORE", uid_set, how, f"({flag})")
    if status != "OK":
        raise ToolError(f"Не удалось изменить пометки писем {uid_set} в «{folder}»: {folders.said(data)}")


def _move(imap: imaplib.IMAP4, uid_set: str, target: str) -> None:
    if "MOVE" in imap.capabilities:  # RFC 6851
        status, data = imap.uid("MOVE", uid_set, folders.quote(target))
        if status != "OK":
            raise ToolError(f"Не удалось переложить письма {uid_set} в «{target}»: {folders.said(data)}")

        return

    status, data = imap.uid("COPY", uid_set, folders.quote(target))
    if status != "OK":
        raise ToolError(f"Не удалось переложить письма {uid_set} в «{target}»: {folders.said(data)}")

    status, data = imap.uid("STORE", uid_set, "+FLAGS", r"(\Deleted)")
    if status != "OK":
        raise ToolError(f"Письма {uid_set} скопированы в «{target}», но оригиналы не помечены удалёнными: {folders.said(data)}")

    # A plain EXPUNGE would also purge everything else the user flagged \Deleted;
    # without UIDPLUS (RFC 4315) the originals just stay flagged, which search skips.
    if "UIDPLUS" in imap.capabilities:
        status, data = imap.uid("EXPUNGE", uid_set)
        if status != "OK":
            raise ToolError(f"Письма {uid_set} скопированы в «{target}», но оригиналы не убраны: {folders.said(data)}")


def folder_list(config: dict[str, Any], secret: str) -> list[dict[str, Any]]:
    """The folders mail can be in, with the role of the special ones."""
    with _imap(config, secret) as imap:
        found = folders.list_folders(imap)

    role = folders.roles(found)

    return [{"name": folder.name, "role": role.get(folder.name)} for folder in found if folder.selectable]


def build_message(
    config: dict[str, Any],
    *,
    to: list[str],
    cc: list[str],
    subject: str,
    body: str,
    attachments: list[Path],
    bcc: list[str] = (),
    reply_to: str | None = None,
    parent: dict[str, Any] | None = None,
) -> EmailMessage:
    """`parent` (thread_of): the letter this one answers — the thread headers mail
    clients group by, and Re: before the subject unless it is there."""
    message = EmailMessage()
    message["From"] = config["email"]
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    # Kept in our copies (Drafts, Sent) as mail clients do; smtplib leaves it out of what goes out.
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    if reply_to:
        message["Reply-To"] = reply_to

    # A line break from a model's text is not a header error: one line, as mail clients do.
    subject = " ".join(subject.split())
    if parent is not None:
        # One «Re:», however many the subject came with («Re: Re: Бюджет» from a template).
        while _RE_RE.match(subject):
            subject = _RE_RE.sub("", subject, count=1).lstrip()
        subject = f"Re: {subject}"
    message["Subject"] = subject

    if parent is not None and parent["message_id"]:
        ancestors = parent["references"] or [parent["in_reply_to"]] * bool(parent["in_reply_to"])
        message["In-Reply-To"] = parent["message_id"]
        message["References"] = " ".join([*ancestors, parent["message_id"]])

    message["Date"] = formatdate(localtime=True)
    # The sender's domain, not this machine's name: that is no one's business, and spam filters frown at it.
    message["Message-ID"] = make_msgid(domain=config["email"].rsplit("@", 1)[-1])
    message.set_content(body)

    for path in attachments:
        mime, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)

    return message


class DeliveryUnknown(ToolError):
    """The connection broke while the letter itself was going: it may have been delivered."""


def send(config: dict[str, Any], secret: str, message: EmailMessage) -> dict[str, str]:
    """The recipients the server refused (address → its answer); the others got it."""
    with _smtp(config, secret) as smtp:
        # Which part of the talk broke: before DATA nothing went out, a refusal of the
        # data is a refusal; a connection lost during it leaves the letter's fate unknown.
        data = smtp.data
        sending = []

        def data_phase(msg):
            sending.append(True)
            return data(msg)

        smtp.data = data_phase

        try:
            # Cyrillic text goes as 8-bit (and so does a forwarded letter, as it came): said
            # so to a server that takes it, as RFC 6152 asks; smtplib never says it.
            options = ["BODY=8BITMIME"] if smtp.has_extn("8bitmime") and _is_8bit(message) else []
            refused = smtp.send_message(message, mail_options=options)

        except smtplib.SMTPRecipientsRefused as e:
            raise ToolError(f"Сервер не принял ни одного адресата: {_refusals(e.recipients)}") from e

        except smtplib.SMTPResponseException as e:
            # The server answered with a refusal: nothing went out.
            raise ToolError(f"Не удалось отправить письмо: {e}") from e

        # SMTPException is an OSError too: a lost connection, a timeout.
        except OSError as e:
            if sending:
                raise DeliveryUnknown(f"Связь с сервером оборвалась, пока письмо передавалось: {e or type(e).__name__}") from e
            raise ToolError(f"Не удалось отправить письмо: {e}") from e

    return _refusals(refused)


def _is_8bit(message: EmailMessage) -> bool:
    return any(byte > 0x7F for byte in message.as_bytes())


def _refusals(recipients: dict[str, tuple[int, bytes]]) -> dict[str, str]:
    return {address: f"{code} {text.decode(errors='replace')}" for address, (code, text) in recipients.items()}


def append_draft(config: dict[str, Any], secret: str, message: EmailMessage) -> str:
    """Save into the provider's Drafts folder (the one flagged \\Drafts)."""
    with _imap(config, secret) as imap:
        folder = config.get("drafts_folder") or folders.special_folder(imap, "drafts")

        status, data = imap.append(folders.quote(folder), r"(\Draft \Seen)", None, message.as_bytes())
        if status != "OK":
            raise ToolError(f"Не удалось сохранить черновик: {folders.said(data)}")

    return folder


def saves_sent_copy(config: dict[str, Any]) -> bool:
    """Whether a sent letter needs a copy in Sent from us: SMTP only delivers it, and the
    mail client keeps the copy. `save_sent: no` for servers that keep one themselves
    (Gmail, Outlook), or every letter would be there twice."""
    return config.get("save_sent") != "no"


def append_sent(config: dict[str, Any], secret: str, message: EmailMessage) -> str:
    """Keep a sent letter in the provider's Sent folder (the one flagged \\Sent)."""
    with _imap(config, secret) as imap:
        folder = config.get("sent_folder") or folders.special_folder(imap, "sent")

        status, data = imap.append(folders.quote(folder), r"(\Seen)", None, message.as_bytes())
        if status != "OK":
            raise ToolError(f"Не удалось сохранить письмо в «{folder}»: {folders.said(data)}")

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
            # __exit__ does not run when __enter__ fails: the socket is closed here.
            _close(getattr(self, "conn", None))
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
            _close(getattr(self, "conn", None))
            raise ToolError(f"Не удалось войти в SMTP {host}:{port} ({security}): {e}") from e

        return self.conn


    def __exit__(self, *exc) -> None:
        try:
            self.conn.quit()

        except (smtplib.SMTPException, OSError):
            pass


def _close(conn) -> None:
    """A connection whose login failed: only its socket, there is nothing to say to the server."""
    try:
        if isinstance(conn, smtplib.SMTP):
            conn.close()
        elif conn is not None:
            conn.shutdown()

    except (OSError, imaplib.IMAP4.error):
        pass


def _select(imap: imaplib.IMAP4, folder: str, *, readonly: bool) -> None:
    status, data = imap.select(folders.quote(folders.on_server(imap, folder)), readonly=readonly)
    if status != "OK":
        raise ToolError(f"Папка почты «{folder}» не найдена: {folders.said(data)}")


def _attachment_name(filename: str, n: int) -> str:
    """The sender names the file: only its last part, nothing Windows refuses, and never
    `..` or empty."""
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip(" .")

    return name or f"attachment_{n}"


def _unnamed(file: letter.File, n: int) -> str:
    """A file the sender gave no name: by its number and its type (an inline picture, a PDF as the whole letter)."""
    return f"attachment_{n}{mimetypes.guess_extension(file.content_type) or ''}"


def _save_file(folder: Path, name: str, content: bytes) -> Path:
    """Two attachments called image.png: the second becomes image (2).png. The same file
    again (the letter read once more, its next part of the text) is the one already saved."""
    folder.mkdir(parents=True, exist_ok=True)
    target, stem, suffix = folder / name, Path(name).stem, Path(name).suffix

    for copy in range(2, 1000):
        if not target.exists():
            break
        if target.read_bytes() == content:
            return target
        target = folder / f"{stem} ({copy}){suffix}"

    target.write_bytes(content)

    return target


def address_of(header: str) -> str:
    """`Иван Петров <ivan@company.local>` → `ivan@company.local`; what a reply needs."""
    return parseaddr(header)[1].strip()


def _header(message, name: str) -> str:
    value = message.get(name)
    if value is None:
        return ""

    try:
        text = str(make_header(decode_header(str(value))))

    except (ValueError, LookupError):
        text = str(value)

    return letter.header_text(message, name, text) if isinstance(message, EmailMessage) else text


def _iso_date(value) -> str | None:
    if not value:
        return None

    try:
        return parsedate_to_datetime(str(value)).isoformat()

    except (TypeError, ValueError):
        return None
