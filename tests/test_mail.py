"""Mail adapter: which connection security a given account configuration gets."""

from __future__ import annotations

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp


@pytest.mark.parametrize(
    ("config", "imap", "smtp"),
    [
        ({}, "ssl", "ssl"),                                   # old accounts: unchanged behaviour
        ({"smtp_port": 587}, "ssl", "starttls"),              # outlook-style preset
        ({"security": "none"}, "none", "none"),
        ({"security": "none", "smtp_security": "starttls"}, "none", "starttls"),
    ],
)
def test_connection_security(config, imap, smtp):
    assert imap_smtp.connection_security(config, "imap") == imap
    assert imap_smtp.connection_security(config, "smtp") == smtp


def test_unknown_security_is_an_error():
    with pytest.raises(ToolError):
        imap_smtp.connection_security({"security": "tls13"}, "imap")


@pytest.mark.parametrize("address", ["boss@company.local", "ivanov@company.ru", "a.b+tag@mail.test", " boss@corp.lan "])
def test_mail_accepts_internal_domains(address):
    from internal_tools.mail.send import Input

    assert Input(account="work", to=[address], subject="s", body="b").to == [address.strip()]


@pytest.mark.parametrize("address", ["не адрес", "a@b@c", "boss@", "@company.ru", "a b@c.ru", "x@y.ru, z@w.ru"])
def test_mail_rejects_malformed_addresses(address):
    from pydantic import ValidationError

    from internal_tools.mail.send import Input

    with pytest.raises(ValidationError):
        Input(account="work", to=[address], subject="s", body="b")


@pytest.mark.parametrize(
    ("header", "address"),
    [
        ("Иван Петров <ivan@company.local>", "ivan@company.local"),
        ('"Отдел продаж" <sales@corp.lan>', "sales@corp.lan"),
        ("client@test.ru", "client@test.ru"),
    ],
)
def test_sender_address_is_extracted(header, address):
    assert imap_smtp.address_of(header) == address


def test_search_output_describes_sender_email():
    from common.tools import discover

    fields = discover()["mail.search"].manifest()["output_schema"]["$defs"]["MailHeader"]["properties"]

    assert {"sender", "sender_email"} <= set(fields)


@pytest.mark.parametrize("entry", ["boss@company.ru", "*@company.local", " *@Corp.LAN "])
def test_sender_masks_accept_addresses_and_domains(entry):
    from internal_tools.mail.search import Input

    assert Input(account="w", sender_in=[entry]).sender_in == [entry.strip().lower()]


@pytest.mark.parametrize("entry", ["*", "*@*", "*@", "boss", "a@b@c"])
def test_sender_masks_reject_too_broad_or_malformed(entry):
    from pydantic import ValidationError

    from internal_tools.mail.search import Input

    with pytest.raises(ValidationError):
        Input(account="w", sender_in=[entry])


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("client@test.local", True),
        ("Client@TEST.local", True),
        ("boss@other.ru", True),            # listed exactly
        ("x@evil-test.local.com", False),   # a substring match would let this through
        ("test.local@evil.com", False),
        ("boss2@other.ru", False),
    ],
)
def test_sender_in_compares_the_address_exactly(address, expected):
    item = {"from": f"Кто-то <{address}>", "from_email": address, "subject": "Заявка"}

    assert imap_smtp.matches(item, sender=None, subject=None, sender_in=["*@test.local", "boss@other.ru"]) is expected


HEADER = {"from": "Иван Петров <ivan@company.local>", "subject": "Заявка на поставку"}


@pytest.mark.parametrize("sender", ["company.local", "IVAN@", "Петров", None])
def test_sender_filter_matches_name_address_or_domain(sender):
    assert imap_smtp.matches(HEADER, sender=sender, subject="заявка")


@pytest.mark.parametrize(("sender", "subject"), [("other.local", None), (None, "Счёт")])
def test_filters_reject_other_mail(sender, subject):
    assert not imap_smtp.matches(HEADER, sender=sender, subject=subject)


@pytest.mark.parametrize(
    ("name", "encoded"),
    [("INBOX", "INBOX"), ("A&B", "A&-B"), ("Обработано", "&BB4EMQRABDAEMQQ+BEIEMAQ9BD4-"), ("Отчёты/2026", "&BB4EQgRHBFEEQgRL-/2026")],
)
def test_folder_names_use_modified_utf7(name, encoded):
    assert imap_smtp.encode_folder(name) == encoded
    assert imap_smtp.decode_folder(encoded) == name


def test_mark_needs_something_to_do():
    from pydantic import ValidationError

    from internal_tools.mail.mark import Input

    with pytest.raises(ValidationError):
        Input(account="work", uid=1)

    assert Input(account="work", uid=1, seen=True).seen is True


async def test_mark_touches_nothing_in_a_dry_run(monkeypatch):
    from internal_tools.mail.mark import Input, mark

    monkeypatch.setattr(imap_smtp, "mark", lambda *a, **k: pytest.fail("a dry run marked a letter"))
    ctx = _send_context("run", {}, dry_run=True)

    out = await mark(Input(account="work", uid=7, seen=True, move_to="Обработано"), ctx)

    assert out.folder == "Обработано"
    assert ctx.logs == ["Пробный запуск: письмо 7 в «INBOX» не тронуто; было бы: прочитано — да, переложить в «Обработано»"]


class FakeIMAP:
    def __init__(self, capabilities, uids=(b"7",)):
        self.capabilities = capabilities
        self.commands: list[tuple] = []
        self.uids = uids


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def list(self, *args):
        return "OK", [b'(\\HasNoChildren) "/" "Archive"']


    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b" ".join(self.uids)]

        self.commands.append((command, *args))
        return "OK", [b"done"]


    def expunge(self):
        self.commands.append(("EXPUNGE-ALL",))
        return "OK", [None]


@pytest.mark.parametrize(
    ("capabilities", "commands"),
    [
        (("MOVE", "UIDPLUS"), ["MOVE"]),
        (("UIDPLUS",), ["COPY", "STORE", "EXPUNGE"]),  # only this message is purged
        ((), ["COPY", "STORE"]),                       # nothing purged: a plain EXPUNGE would take the user's deleted mail
    ],
)
def test_move_never_purges_other_mail(monkeypatch, capabilities, commands):
    fake = FakeIMAP(capabilities)
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: fake)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    assert imap_smtp.mark({}, "", folder="INBOX", uid=7, seen=None, move_to="Archive") == "Archive"
    assert [c[0] for c in fake.commands] == commands
    assert all(c[1] == "7" for c in fake.commands)


def test_moving_a_letter_that_is_gone_is_an_error(monkeypatch):
    """MOVE of a UID that is not there answers OK: without a look first it would report success."""
    fake = FakeIMAP(("MOVE",), uids=())
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: fake)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    with pytest.raises(ToolError, match="не найдено"):
        imap_smtp.mark({}, "", folder="INBOX", uid=7, seen=None, move_to="Archive")
    assert fake.commands == []


def _send_context(mode, task_settings, *, dry_run=False):
    from common.tools import ToolContext
    from common.tools.context import Credential

    return ToolContext(
        mode=mode, settings=None, paths=None, llm=None,
        credentials={"account": Credential(name="work", kind="imap_smtp", config={"email": "agent@company.local"}, secret="")},
        dry_run=dry_run, task_settings=task_settings,
    )


@pytest.mark.parametrize(
    ("mode", "task_settings", "dry_run", "expected"),
    [
        ("run", {}, False, "draft"),                                     # a task drafts by default
        ("run", {"mail": {"send_directly": False}}, False, "draft"),
        ("run", {"mail": {"send_directly": True}}, False, "sent"),       # the user let it send
        ("run", {"mail": {"send_directly": True}}, True, "draft"),       # a dry run never sends
        ("chat", {}, False, "sent"),                                     # chat: the user just confirmed
    ],
)
async def test_send_follows_the_task_mail_settings(monkeypatch, mode, task_settings, dry_run, expected):
    from internal_tools.mail.send import Input, send

    monkeypatch.setattr(imap_smtp, "append_draft", lambda *a: "Drafts")
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: "Sent")
    monkeypatch.setattr(imap_smtp, "send", lambda *a: {})

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), _send_context(mode, task_settings, dry_run=dry_run))

    assert out.mode == expected
    assert out.folder == ("Drafts" if expected == "draft" else "Sent")


def _sent_context(config):
    from common.tools import ToolContext
    from common.tools.context import Credential

    return ToolContext(
        mode="chat", settings=None, paths=None, llm=None,
        credentials={"account": Credential(name="work", kind="imap_smtp", config={"email": "agent@company.local", **config}, secret="")},
    )


async def test_a_server_that_keeps_sent_mail_gets_no_second_copy(monkeypatch):
    from internal_tools.mail.adapters.presets import PRESETS
    from internal_tools.mail.send import Input, send

    monkeypatch.setattr(imap_smtp, "send", lambda *a: {})
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: pytest.fail("Gmail keeps the copy itself"))

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), _sent_context(PRESETS["gmail"]))

    assert out.mode == "sent" and out.folder is None


async def test_a_failed_copy_does_not_fail_a_sent_letter(monkeypatch):
    from internal_tools.mail.send import Input, send

    def no_sent_folder(*a):
        raise ToolError("Нет папки отправленных и не удалось её создать")

    def connection_lost(*a):
        raise TimeoutError("timed out")

    sent = []
    monkeypatch.setattr(imap_smtp, "send", lambda *a: sent.append(a) or {})
    monkeypatch.setattr(imap_smtp, "append_sent", no_sent_folder)

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), _sent_context({}))

    # Sent once; an error here would make the agent try again and send it twice.
    assert len(sent) == 1
    assert out.mode == "sent" and out.folder is None and "отправленных" in out.copy_error

    # Not only our own errors: a lost connection during APPEND is not a failed send either.
    monkeypatch.setattr(imap_smtp, "append_sent", connection_lost)
    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), _sent_context({}))
    assert len(sent) == 2 and out.mode == "sent" and "timed out" in out.copy_error


class FakeFolders:
    def __init__(self, rows):
        self.rows = rows
        self.created: list[str] = []


    def list(self):
        return "OK", [row.encode() for row in self.rows]


    def create(self, folder):
        self.created.append(folder)
        return "OK", [b""]


@pytest.mark.parametrize(
    ("rows", "folder", "created"),
    [
        (['(\\HasNoChildren) "/" "INBOX"', '(\\HasNoChildren \\Sent) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"'], "Отправленные", []),
        (['(\\HasNoChildren) "/" "INBOX"', '(\\HasNoChildren) "/" "Sent"'], "Sent", []),
        (['(\\HasNoChildren) "/" "INBOX"'], "Sent", ['"Sent"']),  # GreenMail starts with INBOX only
    ],
)
def test_sent_folder_is_the_flagged_one_or_sent(rows, folder, created):
    imap = FakeFolders(rows)

    assert imap_smtp._special_folder(imap, "\\Sent", "Sent", "отправленных") == folder
    assert imap.created == created


class FakeMailbox:
    """INBOX with uids 1..n; every third letter comes from the company."""

    capabilities = ()

    def __init__(self, count):
        self.count = count
        self.fetches: list[str] = []


    def select(self, folder, readonly=False):
        return "OK", [str(self.count).encode()]


    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(str(u) for u in range(1, self.count + 1)).encode()]

        self.fetches.append(args[0])
        parts = []
        # Servers answer in their own order, not the order asked.
        for uid in sorted(int(u) for u in args[0].split(",")):
            sender = "boss@company.ru" if uid % 3 == 0 else f"user{uid}@other.ru"
            raw = f"From: {sender}\r\nTo: agent@company.ru\r\nSubject: Письмо {uid}\r\n\r\n".encode()
            parts += [(f"{uid} (UID {uid} BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {{{len(raw)}}}".encode(), raw), b")"]

        return "OK", parts


def test_search_fetches_headers_in_batches_newest_first(monkeypatch):
    box = FakeMailbox(500)
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: box)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    found = imap_smtp.search({}, "", folder="INBOX", sender=None, subject=None, since=None, unseen=False, limit=20, sender_in=["*@company.ru"])

    assert [item["uid"] for item in found][:3] == [498, 495, 492]
    assert len(found) == 20 and all(item["from_email"] == "boss@company.ru" for item in found)
    assert len(box.fetches) == 2


@pytest.mark.parametrize("folder", ['"INBOX"\r\nA1 DELETE "Важное"', 'Счета "ООО"', "a\\b", "INBOX"])
def test_a_folder_name_stays_one_imap_string(folder):
    from internal_tools.mail.adapters.imap_smtp import _quote, _unquote, decode_folder

    quoted = _quote(folder)

    assert "\r" not in quoted and "\n" not in quoted
    assert decode_folder(_unquote(quoted)) == folder


@pytest.mark.parametrize(("sent", "saved"), [("..", "attachment_1"), ("../../x.pdf", "x.pdf"), ("C:\\tmp\\a:b.pdf", "a_b.pdf")])
def test_an_attachment_is_saved_under_a_safe_name(sent, saved):
    from internal_tools.mail.adapters.imap_smtp import _attachment_name

    assert _attachment_name(sent, 1) == saved



async def test_recipients_the_server_refused_are_named(monkeypatch):
    from internal_tools.mail.send import Input, send

    monkeypatch.setattr(imap_smtp, "send", lambda *a: {"typo@company.local": "550 no such user"})
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: "Sent")

    out = await send(Input(account="work", to=["boss@company.local"], cc=["typo@company.local"], subject="s", body="b"), _sent_context({}))

    assert out.mode == "sent" and out.refused == {"typo@company.local": "550 no such user"}


def test_a_subject_with_line_breaks_is_one_line():
    message = imap_smtp.build_message({"email": "a@company.local"}, to=["b@company.local"], cc=[], subject="Отчёт\r\nза май", body="", attachments=[])

    assert message["Subject"] == "Отчёт за май"

