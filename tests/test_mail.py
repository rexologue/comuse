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


def test_mark_is_skipped_in_dry_runs():
    from common.tools import discover

    spec = discover()["mail.mark"]
    assert (spec.side_effect, spec.dry_run) == ("write", "skip")


class FakeIMAP:
    def __init__(self, capabilities):
        self.capabilities = capabilities
        self.commands: list[tuple] = []


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def list(self, *args):
        return "OK", [b'(\\HasNoChildren) "/" "Archive"']


    def uid(self, command, *args):
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


def _send_context(mode, task_settings, *, draft=False):
    from common.tools import ToolContext
    from common.tools.context import MailAccount

    return ToolContext(
        mode=mode, settings=None, paths=None, llm=None,
        get_account=lambda name: MailAccount(name=name, config={"email": "agent@company.local"}, secret=""),
        publish=None, draft=draft, task_settings=task_settings,
    )


@pytest.mark.parametrize(
    ("mode", "task_settings", "draft", "expected"),
    [
        ("run", {}, False, "draft"),                                     # a task drafts by default
        ("run", {"mail": {"send_directly": False}}, False, "draft"),
        ("run", {"mail": {"send_directly": True}}, False, "sent"),       # the user let it send
        ("run", {"mail": {"send_directly": True}}, True, "draft"),       # a dry run never sends
        ("chat", {}, False, "sent"),                                     # chat: the user just confirmed
    ],
)
async def test_send_follows_the_task_mail_settings(monkeypatch, mode, task_settings, draft, expected):
    from internal_tools.mail.send import Input, send

    monkeypatch.setattr(imap_smtp, "append_draft", lambda *a: "Drafts")
    monkeypatch.setattr(imap_smtp, "send", lambda *a: None)

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), _send_context(mode, task_settings, draft=draft))

    assert out.mode == expected
