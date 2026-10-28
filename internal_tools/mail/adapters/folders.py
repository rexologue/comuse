"""Mail folders: their names on the wire (modified UTF-7, quoted), the server's list of
them, and the special ones — sent, drafts, archive, junk, trash — found as mail
clients find them: by the flag the server gives (RFC 6154), else by a name in use
for it (Exchange's Sent Items, Gmail's [Gmail]/Sent Mail, cPanel's INBOX.Sent,
Russian names). A missing one is created next to the others, not beside them."""

from __future__ import annotations

import base64
import binascii
import imaplib
import re
from dataclasses import dataclass
from typing import Any

from common.tools import ToolError


# role → the server's flag, the names it goes by (the last part of a folder's name,
# any case), and the one a new folder gets.
ROLES: dict[str, tuple[str, tuple[str, ...], str]] = {
    "sent": ("\\sent", ("sent", "sent items", "sent mail", "sent messages", "отправленные"), "Sent"),
    "drafts": ("\\drafts", ("drafts", "черновики"), "Drafts"),
    "archive": ("\\archive", ("archive", "archives", "all mail", "архив"), "Archive"),
    "junk": ("\\junk", ("junk", "spam", "junk e-mail", "junk email", "bulk mail", "спам"), "Junk"),
    "trash": ("\\trash", ("trash", "deleted items", "deleted messages", "deleted", "bin", "корзина", "удалённые", "удаленные"), "Trash"),
}

_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<sep>NIL|"(?:[^"\\]|\\.)*")\s+(?P<name>.*)$', re.IGNORECASE)
_LITERAL_RE = re.compile(r"\{\d+\}$")
_UTF7_RE = re.compile(r"&([^-]*)-")


@dataclass
class Folder:
    name: str
    delimiter: str | None
    # Lower case: \noselect, \sent, \haschildren…
    flags: frozenset[str]


    @property
    def selectable(self) -> bool:
        return not self.flags & {"\\noselect", "\\nonexistent"}


    @property
    def leaf(self) -> str:
        return self.name.rsplit(self.delimiter, 1)[-1] if self.delimiter else self.name


def parse_list(rows: list) -> list[Folder]:
    """LIST answers as imaplib gives them: a line per folder, or (line, name) when the
    server sent the name as a literal. A line nothing can be made of is skipped."""
    folders: list[Folder] = []

    for row in rows or []:
        literal = None
        if isinstance(row, tuple):
            row, literal = row[0], row[1]
        if not isinstance(row, bytes) or not row:
            continue

        match = _LIST_RE.match(row.decode(errors="replace").strip())
        if not match:
            continue

        sep = match.group("sep")
        name = match.group("name")
        if literal is not None and _LITERAL_RE.search(name):
            name = literal.decode(errors="replace")

        folders.append(Folder(
            name=decode_folder(unquote(name)),
            delimiter=None if sep.upper() == "NIL" else unquote(sep),
            flags=frozenset(flag.lower() for flag in match.group("flags").split()),
        ))

    return folders


def list_folders(imap: imaplib.IMAP4, pattern: str = "*") -> list[Folder]:
    status, rows = imap.list('""', quote(pattern) if pattern != "*" else "*")

    return parse_list(rows) if status == "OK" else []


def roles(folders: list[Folder]) -> dict[str, str]:
    """Folder name → its role: inbox and those of ROLES the server has."""
    found = {folder.name: "inbox" for folder in folders if folder.name.upper() == "INBOX"}

    for role in ROLES:
        if (folder := find(folders, role)) is not None:
            found.setdefault(folder.name, role)

    return found


def find(folders: list[Folder], role: str) -> Folder | None:
    """The folder the server flags for the role, else the one named for it (the first
    name of the list that some folder has, the shortest such folder)."""
    flag, names, _ = ROLES[role]
    selectable = [folder for folder in folders if folder.selectable]

    flagged = [folder for folder in selectable if flag in folder.flags]
    if flagged:
        return min(flagged, key=lambda folder: len(folder.name))

    for name in names:
        named = [folder for folder in selectable if folder.leaf.lower() == name]
        if named:
            return min(named, key=lambda folder: len(folder.name))

    return None


def special_folder(imap: imaplib.IMAP4, role: str) -> str:
    """The role's folder; made if the server has none (GreenMail starts with INBOX only)."""
    folders = list_folders(imap)
    found = find(folders, role)
    if found is not None:
        return found.name

    name = _prefix(folders) + ROLES[role][2]
    status, data = imap.create(quote(name))
    if status != "OK" and b"ALREADYEXISTS" not in b" ".join(item for item in data or [] if isinstance(item, bytes)).upper():
        raise ToolError(f"Нет папки «{ROLES[role][2]}» и не удалось её создать: {said(data)}")

    return name


def _prefix(folders: list[Folder]) -> str:
    """Where new folders go: under INBOX on servers that keep everything there (Courier,
    cPanel: INBOX.Sent), else at the top."""
    others = [folder for folder in folders if folder.name.upper() != "INBOX"]
    inbox = next((folder for folder in folders if folder.name.upper() == "INBOX"), None)

    if others and inbox is not None and inbox.delimiter and all(folder.name.upper().startswith("INBOX" + inbox.delimiter) for folder in others):
        return "INBOX" + inbox.delimiter

    return ""


def said(data: Any) -> str:
    """What the server answered, as text: [b'EXAMINE failed. No such mailbox'] → «EXAMINE failed. No such mailbox»."""
    items = data if isinstance(data, (list, tuple)) else [data]
    parts = [item.decode(errors="replace") if isinstance(item, bytes) else str(item) for item in items if item not in (None, b"")]

    return "; ".join(parts) or "сервер не объяснил"


def on_server(imap: imaplib.IMAP4, name: str) -> str:
    """A folder as the person writes it, «Рассылки/Новости», as this server names it: its
    hierarchy delimiter for the slash («Рассылки.Новости» where it is «.») — unless a
    folder of that very name is there."""
    if "/" not in name:
        return name

    found = list_folders(imap)
    if any(folder.name == name for folder in found):
        return name

    delimiter = next((folder.delimiter for folder in found if folder.delimiter), None)
    if delimiter is None:
        status, rows = imap.list('""', '""')
        root = parse_list(rows) if status == "OK" else []
        delimiter = root[0].delimiter if root else None

    return name.replace("/", delimiter) if delimiter and delimiter != "/" else name


def ensure_folder(imap: imaplib.IMAP4, name: str) -> None:
    """A folder to put mail in: there and selectable, or made (a \\Noselect placeholder
    of that name is not a folder mail can go to)."""
    if any(folder.name == name and folder.selectable for folder in list_folders(imap, name)):
        return

    status, data = imap.create(quote(name))
    if status != "OK":
        raise ToolError(f"Не удалось создать папку почты «{name}»: {said(data)}")


def quote(folder: str) -> str:
    """A folder as one IMAP quoted string, whatever the name: it may come from a letter
    the model read, so nothing in it can end the string or the command."""
    encoded = encode_folder(folder).replace("\\", "\\\\").replace('"', '\\"')

    return f'"{encoded}"'


def unquote(name: str) -> str:
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
    """A part that is not valid modified UTF-7 stays as it came: a server's odd name is
    still a name, not a reason to fail."""
    def decode(match: re.Match) -> str:
        chunk = match.group(1)
        if not chunk:
            return "&"

        raw = chunk.replace(",", "/")
        try:
            return base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True).decode("utf-16-be")

        except (binascii.Error, ValueError):
            return match.group(0)

    return _UTF7_RE.sub(decode, name)
