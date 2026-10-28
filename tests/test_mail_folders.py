"""Mail folders as mail clients find them, and what mail.mark does to letters in them:
many at once, flags, a move to a folder or to the mailbox's archive, junk or trash —
never a purge of other mail, never a success for a letter that is not there."""

from __future__ import annotations

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import folders, imap_smtp
from tests.test_mail import _send_context


@pytest.mark.parametrize(
    ("name", "encoded"),
    [("INBOX", "INBOX"), ("A&B", "A&-B"), ("Обработано", "&BB4EMQRABDAEMQQ+BEIEMAQ9BD4-"), ("Отчёты/2026", "&BB4EQgRHBFEEQgRL-/2026")],
)
def test_folder_names_use_modified_utf7(name, encoded):
    assert folders.encode_folder(name) == encoded
    assert folders.decode_folder(encoded) == name


def test_a_name_that_is_not_utf7_stays_as_it_came():
    assert folders.decode_folder("Old&BB4-x&!!!-") == "OldОx&!!!-"


@pytest.mark.parametrize("folder", ['"INBOX"\r\nA1 DELETE "Важное"', 'Счета "ООО"', "a\\b", "INBOX"])
def test_a_folder_name_stays_one_imap_string(folder):
    quoted = folders.quote(folder)

    assert "\r" not in quoted and "\n" not in quoted
    assert folders.decode_folder(folders.unquote(quoted)) == folder


def test_list_answers_as_servers_give_them():
    rows = [
        b'(\\HasNoChildren) "/" "INBOX"',
        b'(\\HasNoChildren) NIL "Flat"',                                   # no hierarchy
        (b'(\\HasNoChildren) "." {11}', b'Some "name"'),                  # the name as a literal
        b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
        b"garbage",
    ]

    found = folders.parse_list(rows)

    assert [(f.name, f.delimiter, f.selectable) for f in found] == [
        ("INBOX", "/", True), ("Flat", None, True), ('Some "name"', ".", True), ("[Gmail]", "/", False),
    ]


def _folders(*rows: str) -> list[folders.Folder]:
    return folders.parse_list([row.encode() for row in rows])


@pytest.mark.parametrize(
    ("rows", "role", "name"),
    [
        (['(\\HasNoChildren \\Sent) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"', '() "/" "Sent"'], "sent", "Отправленные"),  # the flag first
        (['() "/" "INBOX"', '() "/" "Sent Items"'], "sent", "Sent Items"),                                       # Exchange
        (['() "." "INBOX"', '() "." "INBOX.Sent"', '() "." "INBOX.Drafts"'], "drafts", "INBOX.Drafts"),          # cPanel
        (['(\\Noselect) "/" "[Gmail]"', '() "/" "[Gmail]/All Mail"', '() "/" "[Gmail]/Spam"'], "archive", "[Gmail]/All Mail"),
        (['() "/" "[Gmail]/Spam"'], "junk", "[Gmail]/Spam"),
        (['() "/" "&BBoEPgRABDcEOAQ9BDA-"'], "trash", "Корзина"),
        (['() "/" "INBOX"', '() "/" "Sentimental"'], "sent", None),                                             # a name, not a substring
    ],
)
def test_special_folders_are_found_as_mail_clients_find_them(rows, role, name):
    found = folders.find(_folders(*rows), role)

    assert (found.name if found else None) == name


def test_roles_name_the_special_folders():
    found = _folders('() "/" "INBOX"', '(\\Sent) "/" "Sent"', '() "/" "Projects"', '() "/" "Trash"')

    assert folders.roles(found) == {"INBOX": "inbox", "Sent": "sent", "Trash": "trash"}


class FakeFolders:
    def __init__(self, *rows: str):
        self.rows = [row.encode() for row in rows]
        self.created: list[str] = []


    def list(self, directory='""', pattern="*"):
        if pattern == "*":
            return "OK", self.rows
        name = folders.unquote(pattern)
        return "OK", [row for row in self.rows if row.decode().endswith(f'"{name}"')] or [None]


    def create(self, folder):
        self.created.append(folder)
        return "OK", [b""]


@pytest.mark.parametrize(
    ("rows", "name", "created"),
    [
        (['() "/" "INBOX"', '() "/" "Sent"'], "Sent", []),
        (['() "/" "INBOX"'], "Sent", ['"Sent"']),                                       # GreenMail starts with INBOX only
        (['() "." "INBOX"', '() "." "INBOX.Drafts"'], "INBOX.Sent", ['"INBOX.Sent"']),   # next to the others
    ],
)
def test_a_missing_special_folder_is_made_next_to_the_others(rows, name, created):
    imap = FakeFolders(*rows)

    assert folders.special_folder(imap, "sent") == name
    assert imap.created == created


def test_a_placeholder_folder_is_not_a_folder_to_put_mail_in():
    imap = FakeFolders('(\\Noselect) "/" "Archive"')

    folders.ensure_folder(imap, "Archive")

    assert imap.created == ['"Archive"']


class FakeIMAP(FakeFolders):
    def __init__(self, capabilities, uids=(b"7",), rows=('() "/" "INBOX"', '() "/" "Archive"', '() "/" "Trash"')):
        super().__init__(*rows)
        self.capabilities = capabilities
        self.commands: list[tuple] = []
        self.uids = uids


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self.uids)]

        self.commands.append((command, *args))
        return "OK", [b"done"]


    def expunge(self):
        self.commands.append(("EXPUNGE-ALL",))
        return "OK", [None]


@pytest.fixture
def imap(monkeypatch):
    def use(fake: FakeIMAP) -> FakeIMAP:
        monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: fake)
        monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)
        return fake

    return use


def _mark(**kw):
    return imap_smtp.mark({}, "", **{"folder": "INBOX", "uids": [7], "flags": {}, **kw})


@pytest.mark.parametrize(
    ("capabilities", "commands"),
    [
        (("MOVE", "UIDPLUS"), ["MOVE"]),
        (("UIDPLUS",), ["COPY", "STORE", "EXPUNGE"]),  # only these letters are purged
        ((), ["COPY", "STORE"]),                       # nothing purged: a plain EXPUNGE would take the user's deleted mail
    ],
)
def test_move_never_purges_other_mail(imap, capabilities, commands):
    fake = imap(FakeIMAP(capabilities))

    assert _mark(move_to="Archive")["folder"] == "Archive"
    assert [c[0] for c in fake.commands] == commands
    assert all(c[1] == "7" for c in fake.commands)


def test_moving_a_letter_that_is_gone_is_an_error(imap):
    """MOVE of a UID that is not there answers OK: without a look first it would report success."""
    fake = imap(FakeIMAP(("MOVE",), uids=()))

    with pytest.raises(ToolError, match="нет"):
        _mark(move_to="Archive")
    assert fake.commands == []


def test_many_letters_at_once_and_those_not_there_are_named(imap):
    fake = imap(FakeIMAP(("MOVE",), uids=(b"3", b"7")))

    done = _mark(uids=[3, 5, 7], flags={"seen": True, "flagged": False})

    assert done == {"uids": [3, 7], "missing": [5], "folder": "INBOX"}
    assert fake.commands == [("STORE", "3,7", "+FLAGS", "(\\Seen)"), ("STORE", "3,7", "-FLAGS", "(\\Flagged)")]


@pytest.mark.parametrize(("role", "folder"), [("trash", "Trash"), ("archive", "Archive"), ("inbox", "INBOX"), ("junk", "Junk")])
def test_a_move_to_a_role_finds_its_folder(imap, role, folder):
    fake = imap(FakeIMAP(("MOVE",)))
    fake.select = lambda name, readonly=False: ("OK", [b"1"])

    done = _mark(folder="Projects", move_to_role=role)

    assert done["folder"] == folder
    assert fake.commands[-1] == ("MOVE", "7", f'"{folder}"')
    assert fake.created == (['"Junk"'] if role == "junk" else [])   # no junk folder there: made


def test_a_failed_store_is_an_error(imap):
    fake = imap(FakeIMAP(("MOVE",)))
    fake.uid = lambda command, *args: ("OK", [b"7"]) if command == "SEARCH" else ("NO", [b"read-only"])

    with pytest.raises(ToolError, match="Не удалось изменить пометки"):
        _mark(flags={"seen": True})


def test_mark_needs_something_to_do_and_one_place_to_go():
    from pydantic import ValidationError

    from internal_tools.mail.mark import Input

    with pytest.raises(ValidationError):
        Input(account="work", uids=[1])

    with pytest.raises(ValidationError):
        Input(account="work", uids=[1], move_to="Archive", move_to_role="archive")

    assert Input(account="work", uids=[1], flagged=True).flags() == {"flagged": True}


async def test_mark_touches_nothing_in_a_dry_run(monkeypatch):
    from internal_tools.mail.mark import Input, mark

    monkeypatch.setattr(imap_smtp, "mark", lambda *a, **k: pytest.fail("a dry run marked a letter"))
    ctx = _send_context("run", {}, dry_run=True)

    out = await mark(Input(account="work", uids=[7, 8], seen=True, flagged=False, move_to_role="trash"), ctx)

    assert out.uids == [7, 8]
    assert ctx.logs == ["Пробный запуск: письма 7, 8 в «INBOX» не тронуты; было бы: прочитано, не важное, переложить в корзину"]


async def test_folders_lists_the_folders_with_their_roles(monkeypatch):
    from internal_tools.mail.folders import Input, folders as list_folders
    from tests.test_mail import _sent_context

    fake = FakeFolders('() "/" "INBOX"', '(\\Noselect) "/" "[Gmail]"', '(\\Sent) "/" "[Gmail]/Sent Mail"', '() "/" "Проекты"')
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: fake)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    out = await list_folders(Input(account="work"), _sent_context({}))

    assert [(f.name, f.role) for f in out.folders] == [("INBOX", "inbox"), ("[Gmail]/Sent Mail", "sent"), ("Проекты", None)]
