"""Sending as mail clients do: a reply stays in its thread, a hidden copy stays hidden,
a forward carries the letter and nothing of its own headers; and a letter whose fate
is unknown is not reported as unsent (sending it again would deliver it twice)."""

from __future__ import annotations

import smtplib

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp
from tests.test_mail import _send_context, _sent_context
from tests.test_mail_read import LETTER, Mailbox, _mime


CONFIG = {"email": "agent@company.ru"}


def _build(**kw):
    return imap_smtp.build_message(CONFIG, **{"to": ["boss@company.ru"], "cc": [], "subject": "Счёт", "body": "b", "attachments": [], **kw})


@pytest.mark.parametrize(
    ("parent", "in_reply_to", "references"),
    [
        ({"message_id": "<m2@x>", "references": ["<m0@x>", "<m1@x>"], "in_reply_to": "<m1@x>"}, "<m2@x>", "<m0@x> <m1@x> <m2@x>"),
        ({"message_id": "<m2@x>", "references": [], "in_reply_to": "<m1@x>"}, "<m2@x>", "<m1@x> <m2@x>"),   # no References: its In-Reply-To
        ({"message_id": "<m2@x>", "references": [], "in_reply_to": None}, "<m2@x>", "<m2@x>"),
        ({"message_id": None, "references": [], "in_reply_to": None}, None, None),                         # nothing to thread by
    ],
)
def test_a_reply_stays_in_its_thread(parent, in_reply_to, references):
    message = _build(parent={"subject": "Счёт", **parent})

    assert (message["In-Reply-To"], message["References"]) == (in_reply_to, references)
    assert message["Subject"] == "Re: Счёт"


@pytest.mark.parametrize("subject", ["Re: Счёт", "RE[2]: Счёт", "Ответ: Счёт", "AW: Счёт"])
def test_a_reply_subject_gets_one_re(subject):
    assert _build(subject=subject, parent={"message_id": None, "references": [], "in_reply_to": None})["Subject"] == subject


def test_the_message_id_names_the_senders_domain():
    assert _build()["Message-ID"].endswith("@company.ru>")


def test_a_hidden_copy_is_in_our_copy_only():
    message = _build(bcc=["secret@company.ru"], reply_to="pay@company.ru")

    assert message["Bcc"] == "secret@company.ru" and message["Reply-To"] == "pay@company.ru"


class FakeSMTP(smtplib.SMTP):
    """smtplib's own send_message over a talk we script: no socket."""

    def __init__(self, fail_at: str | None, error: Exception):
        super().__init__()
        self.fail_at, self.error = fail_at, error
        self.envelope: list[str] = []
        self.sent: bytes | None = None


    def ehlo_or_helo_if_needed(self):
        pass


    def has_extn(self, name):
        return False


    def mail(self, sender, options=()):
        if self.fail_at == "mail":
            raise self.error
        return 250, b"ok"


    def rcpt(self, recipient, options=()):
        self.envelope.append(recipient)
        return 250, b"ok"


    def data(self, msg):
        if self.fail_at == "data":
            raise self.error
        self.sent = msg
        return 250, b"ok"


def _smtp(monkeypatch, fail_at=None, error=None) -> FakeSMTP:
    smtp = FakeSMTP(fail_at, error)
    monkeypatch.setattr(imap_smtp._smtp, "__enter__", lambda self: smtp)
    monkeypatch.setattr(imap_smtp._smtp, "__exit__", lambda self, *exc: None)
    return smtp


def test_a_hidden_copy_goes_in_the_envelope_not_the_letter(monkeypatch):
    smtp = _smtp(monkeypatch)

    imap_smtp.send(CONFIG, "", _build(bcc=["secret@company.ru"]))

    assert smtp.envelope == ["boss@company.ru", "secret@company.ru"]
    assert b"secret@company.ru" not in smtp.sent


@pytest.mark.parametrize(
    ("fail_at", "error", "unknown"),
    [
        ("data", smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), True),
        ("data", TimeoutError("timed out"), True),
        ("data", smtplib.SMTPDataError(554, b"rejected"), False),       # the server said no: nothing went out
        ("mail", smtplib.SMTPServerDisconnected("closed"), False),       # broke before the letter went
    ],
)
def test_a_letter_lost_in_transit_is_unknown(monkeypatch, fail_at, error, unknown):
    _smtp(monkeypatch, fail_at, error)

    with pytest.raises(ToolError) as caught:
        imap_smtp.send(CONFIG, "", _build())

    assert isinstance(caught.value, imap_smtp.DeliveryUnknown) is unknown


async def test_an_unknown_send_is_not_an_error_to_retry(monkeypatch):
    from internal_tools.mail.send import Input, send

    def lost(*a):
        raise imap_smtp.DeliveryUnknown("связь оборвалась")

    monkeypatch.setattr(imap_smtp, "send", lost)
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: pytest.fail("no copy of a letter that may not have gone"))
    ctx = _sent_context({})

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b"), ctx)

    assert out.mode == "unknown" and "Неизвестно, ушло ли письмо" in ctx.notifications[0]


@pytest.mark.parametrize(("mode", "settings", "answered"), [("chat", {}, True), ("run", {}, False), ("run", {"send_directly": True}, True)])
async def test_the_letter_replied_to_is_marked_answered_once_the_reply_is_gone(monkeypatch, mode, settings, answered):
    from internal_tools.mail.send import Input, send

    flags = []
    monkeypatch.setattr(imap_smtp, "thread_of", lambda *a, **k: {"subject": "Счёт", "message_id": "<m@x>", "references": [], "in_reply_to": None})
    monkeypatch.setattr(imap_smtp, "send", lambda *a: {})
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: "Sent")
    monkeypatch.setattr(imap_smtp, "append_draft", lambda *a: "Drafts")
    monkeypatch.setattr(imap_smtp, "set_flag", lambda *a, **k: flags.append((k["uid"], k["flag"])))

    await send(Input(account="work", to=["boss@company.local"], subject="Счёт", body="b", in_reply_to=7), _send_context(mode, settings))

    assert flags == ([(7, r"\Answered")] if answered else [])


async def test_a_flag_that_fails_does_not_fail_a_sent_reply(monkeypatch):
    from internal_tools.mail.send import Input, send

    def broken(*a, **k):
        raise ToolError("Письмо 7 в «INBOX» не найдено")

    monkeypatch.setattr(imap_smtp, "thread_of", lambda *a, **k: {"subject": "s", "message_id": None, "references": [], "in_reply_to": None})
    monkeypatch.setattr(imap_smtp, "send", lambda *a: {})
    monkeypatch.setattr(imap_smtp, "append_sent", lambda *a: "Sent")
    monkeypatch.setattr(imap_smtp, "set_flag", broken)
    ctx = _sent_context({})

    out = await send(Input(account="work", to=["boss@company.local"], subject="s", body="b", in_reply_to=7), ctx)

    assert out.mode == "sent" and "не помечено отвеченным" in ctx.logs[0]


@pytest.fixture
def source(monkeypatch):
    raw = LETTER.replace(b"From:", b"Received: from relay.internal\r\nBcc: hidden@company.ru\r\nFrom:", 1)
    box = Mailbox(raw)
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: box)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)


def _forward(**kw):
    return imap_smtp.forward_message(CONFIG, "", **{"folder": "INBOX", "uid": 7, "to": ["boss@company.ru"], "cc": [], "bcc": [], "note": "Посмотри", "attachments": True, **kw})


def test_a_forward_carries_the_letter_under_the_note(source):
    message = _forward()

    assert message["Subject"] == "Fwd: Счёт"
    text = message.get_body(("plain",)).get_content()
    assert text.startswith("Посмотри\n\n---------- Пересылаемое письмо ----------\nОт: Бухгалтерия <buh@company.ru>")
    assert "Копия: boss@company.ru" in text and text.rstrip().endswith("Счёт во вложении")
    assert [(part.get_filename(), part.get_content()) for part in message.iter_attachments()] == [("счёт.txt", "42 300")]


def test_a_forward_keeps_none_of_the_sources_own_headers(source):
    sent = _forward().as_bytes()

    assert b"relay.internal" not in sent and b"hidden@company.ru" not in sent and b"<m2@company.ru>" not in sent


def test_a_forward_without_its_files(source):
    assert list(_forward(attachments=False).iter_attachments()) == []


def test_a_forwarded_letter_inside_is_forwarded_as_a_letter(monkeypatch):
    raw = ("Subject: Fwd: Акт\r\n" + _mime("Content-Type: text/plain\r\n\r\nсм.", "Content-Type: message/rfc822\r\n\r\nSubject: Акт\r\n\r\nтело")).encode()
    box = Mailbox(raw)
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: box)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    message = _forward()
    inner = next(message.iter_attachments())

    assert message["Subject"] == "Fwd: Акт"          # one Fwd:, not two
    assert inner.get_content_type() == "message/rfc822" and inner.get_filename() == "Акт.eml"
    # The letter inside as it came: its bytes, its headers.
    assert "тело".encode() in message.as_bytes() and b"Subject: Fwd" not in inner.as_bytes()


async def test_a_forward_in_a_dry_run_is_a_draft(monkeypatch, source):
    from internal_tools.mail.forward import Input, forward

    monkeypatch.setattr(imap_smtp, "send", lambda *a: pytest.fail("a dry run sent a letter"))
    monkeypatch.setattr(imap_smtp, "append_draft", lambda *a: "Drafts")

    out = await forward(Input(account="work", uid=7, to=["boss@company.local"]), _send_context("run", {"send_directly": True}, dry_run=True))

    assert out.mode == "draft" and out.folder == "Drafts"
