"""What a task needs from mail to handle new letters: its own label of what it has
handled (not the user's read state), the server's flags and labels to search by, and
reading only the letters of the senders it may act on."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp
from tests.test_mail import _send_context
from tests.test_mail_folders import FakeIMAP
from tests.test_mail_read import LETTER, Mailbox


@pytest.fixture
def imap(monkeypatch):
    def use(fake):
        monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: fake)
        monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)
        return fake

    return use


@pytest.mark.parametrize("label", ["processed", "invoices_done", "v1.2", "a-b"])
def test_a_label_is_an_imap_atom(label):
    from internal_tools.mail.mark import Input

    assert Input(account="work", uids=[1], add_labels=[label]).add_labels == [label]


@pytest.mark.parametrize("label", ["", "обработано", "two words", "\\Seen", "$Junk", "a)b", "x" * 41])
def test_a_label_is_nothing_else(label):
    from internal_tools.mail.mark import Input

    with pytest.raises(ValidationError):
        Input(account="work", uids=[1], add_labels=[label])


class Searching:
    def __init__(self):
        self.criteria: tuple = ()


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def uid(self, command, *args):
        if command == "SEARCH":
            self.criteria = args
            return "OK", [b"4 5"]

        raw = b"From: boss@company.ru\r\nSubject: Hi\r\n\r\n"
        return "OK", [
            (f"1 (UID 4 FLAGS (\\Seen processed) BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {{{len(raw)}}}".encode(), raw), b")",
            # Flags after the headers, as some servers send them.
            (f"2 (BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {{{len(raw)}}}".encode(), raw), b" UID 5 FLAGS (\\Flagged))",
        ]


def test_flags_and_labels_are_searched_by_the_server_and_come_back(imap):
    box = imap(Searching())

    found = imap_smtp.search(
        {}, "", folder="INBOX", sender=None, subject=None, since=None, unseen=False, limit=10,
        flagged=True, answered=False, labeled=["invoices"], not_labeled=["processed", "skipped"],
    )

    assert box.criteria == ("UNDELETED", "FLAGGED", "UNANSWERED", "KEYWORD", "invoices", "UNKEYWORD", "processed", "UNKEYWORD", "skipped")
    assert [(item["uid"], item["flags"]) for item in found] == [(5, ["flagged"]), (4, ["seen", "processed"])]


def test_labels_are_set_and_cleared(imap):
    fake = imap(FakeIMAP(("MOVE",), uids=(b"3", b"7")))

    imap_smtp.mark({}, "", folder="INBOX", uids=[3, 7], flags={}, add_labels=["processed", "invoices"], remove_labels=["todo"])

    assert fake.commands == [("STORE", "3,7", "+FLAGS", "(processed invoices)"), ("STORE", "3,7", "-FLAGS", "(todo)")]


@pytest.mark.parametrize(
    ("permanent", "kept"),
    [
        ([b"(\\Seen \\Flagged \\*)"], True),          # the server keeps any label
        ([b"(\\Seen processed)"], True),              # it keeps this one
        ([b"(\\Seen \\Flagged)"], False),             # none of ours: STORE may say OK and forget it
        ([], True),                                   # the server says nothing: its STORE answer decides
    ],
)
def test_a_server_that_keeps_no_labels_says_so_before_the_task_relies_on_it(imap, permanent, kept):
    fake = imap(FakeIMAP(("MOVE",)))
    fake.untagged_responses = {"PERMANENTFLAGS": permanent}

    if kept:
        imap_smtp.mark({}, "", folder="INBOX", uids=[7], flags={}, add_labels=["processed"])
        assert fake.commands == [("STORE", "7", "+FLAGS", "(processed)")]
    else:
        with pytest.raises(ToolError, match="не хранит свои метки"):
            imap_smtp.mark({}, "", folder="INBOX", uids=[7], flags={}, add_labels=["processed"])
        assert fake.commands == []


async def test_labels_in_a_dry_run_are_only_said(monkeypatch):
    from internal_tools.mail.mark import Input, mark

    monkeypatch.setattr(imap_smtp, "mark", lambda *a, **k: pytest.fail("a dry run labelled a letter"))
    ctx = _send_context("run", {}, dry_run=True)

    await mark(Input(account="work", uids=[7], add_labels=["processed"]), ctx)

    assert ctx.logs == ["Пробный запуск: письма 7 в «INBOX» не тронуты; было бы: метки processed"]


def test_a_letter_from_another_sender_is_not_read(imap, tmp_path):
    imap(Mailbox(LETTER))

    with pytest.raises(ToolError, match="не от \\*@other.ru"):
        imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path, sender_in=["*@other.ru"])

    # Nothing of it was saved.
    assert list(tmp_path.iterdir()) == []

    data = imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path, sender_in=["*@company.ru"])
    assert data["text"] == "Счёт во вложении"


def test_the_compiler_refuses_a_label_the_server_would_not_take():
    """The rule is in the schema the compiler checks a step's arguments by, not only in our code."""
    import jsonschema

    from internal_tools.mail.mark import Input

    items = Input.model_json_schema()["properties"]["add_labels"]["items"]

    jsonschema.validate("invoices_forwarded", items)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate("flagged_итоги", items)
