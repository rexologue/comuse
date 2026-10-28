"""Reading a letter as mail clients do: its text from every text part, its files at any
depth, charsets that lie or are missing, HTML as text; and a mailbox that answers the
way some servers do."""

from __future__ import annotations

from email import message_from_bytes, policy

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp, letter


def _letter(raw: str | bytes):
    return message_from_bytes(raw.encode() if isinstance(raw, str) else raw, policy=policy.default)


def _mime(*parts: str, subtype: str = "mixed", boundary: str = "b") -> str:
    body = "".join(f"--{boundary}\r\n{part}\r\n" for part in parts)
    return f"Content-Type: multipart/{subtype}; boundary={boundary}\r\n\r\n{body}--{boundary}--\r\n"


@pytest.mark.parametrize(
    ("payload", "charset", "text"),
    [
        ("Счёт".encode("utf-8"), "utf-8", "Счёт"),
        ("Счёт".encode("utf-8"), "x-unknown", "Счёт"),        # a label nobody knows
        ("Счёт".encode("utf-8"), None, "Счёт"),               # no label, 8-bit text
        ("Счёт".encode("cp1251"), "utf-8", "Счёт"),           # a label that lies
        ("Счёт".encode("koi8-r"), "koi8-r", "Счёт"),
    ],
)
def test_text_decodes_whatever_the_label_says(payload, charset, text):
    assert letter.decode_text(payload, charset) == text


def test_an_unknown_charset_does_not_break_reading():
    text, files = letter.text_and_files(_letter("Content-Type: text/plain; charset=x-unknown\r\n\r\nhello"))

    assert text == "hello" and files == []


def test_files_are_found_at_any_depth():
    inner = _mime("Content-Type: text/plain\r\nContent-Disposition: attachment; filename=a.txt\r\n\r\nA", boundary="c")
    forwarded = "Content-Type: message/rfc822\r\n\r\nSubject: Акт\r\n\r\nтело акта"
    raw = _mime("Content-Type: text/plain\r\n\r\nПривет", inner, forwarded)

    text, files = letter.text_and_files(_letter(raw))

    assert text == "Привет"
    assert [(f.filename, f.content_type) for f in files] == [("a.txt", "text/plain"), ("Акт.eml", "message/rfc822")]
    assert files[0].content == b"A"
    # A forwarded letter is kept whole, its own headers and text.
    assert b"Subject:" in files[1].content and "тело акта".encode() in files[1].content


def test_a_letter_that_is_only_a_file():
    raw = b"Content-Type: application/pdf\r\nContent-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n"

    text, files = letter.text_and_files(_letter(raw))

    assert text == "" and files[0].content == b"%PDF-" and files[0].filename is None


def test_every_text_part_is_the_text():
    """Apple Mail: text, a picture inline, more text — all the text, the picture as a file."""
    picture = "Content-Type: image/png\r\nContent-Disposition: inline\r\nContent-Transfer-Encoding: base64\r\n\r\niVBORw=="
    raw = _mime("Content-Type: text/plain\r\n\r\nДо картинки", picture, "Content-Type: text/plain\r\n\r\nПосле картинки")

    text, files = letter.text_and_files(_letter(raw))

    assert text == "До картинки\n\nПосле картинки"
    assert [f.content_type for f in files] == ["image/png"]


@pytest.mark.parametrize(
    ("plain", "text"),
    [("Текст письма", "Текст письма"), ("", "Версия HTML")],   # an empty plain part says nothing: the HTML one
)
def test_the_plain_alternative_unless_it_is_empty(plain, text):
    raw = _mime(f"Content-Type: text/plain\r\n\r\n{plain}", "Content-Type: text/html\r\n\r\n<p>Версия HTML</p>", subtype="alternative")

    assert letter.text_and_files(_letter(raw))[0] == text


def test_html_reads_as_a_person_reads_it():
    html = """<html><head><title>t</title><style>p { color: red }</style></head><body>
      <p>Добрый&nbsp;день!</p><p>Счёт&nbsp;&mdash; во&nbsp;вложении.</p>
      <script>alert(1)</script>
      <table><tr><td>Сумма</td><td>42&nbsp;300</td></tr></table>
      <a href="https://pay.example/1">Оплатить</a> <a href="mailto:x@y.ru">x@y.ru</a> <a href="#top">наверх</a>
    </body></html>"""

    assert letter.html_to_text(html) == "Добрый день!\n\nСчёт — во вложении.\n\nСумма 42 300\n\nОплатить (https://pay.example/1) x@y.ru наверх"


def test_a_raw_cp1251_header_is_decoded():
    message = _letter("Subject: Счёт\r\n\r\n".encode("cp1251"))

    assert imap_smtp._header(message, "Subject") == "Счёт"


class Mailbox:
    """A server that answers FETCH the way real ones do."""

    def __init__(self, raw: bytes, size: int | None = None, unsolicited: bool = False):
        self.raw, self.size, self.unsolicited = raw, len(raw) if size is None else size, unsolicited
        self.bodies = 0


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def uid(self, command, uid, items):
        if items.startswith("(FLAGS RFC822.SIZE"):
            return "OK", [f'1 (UID {uid} FLAGS (\\Seen \\Answered \\Recent $Важно) RFC822.SIZE {self.size} INTERNALDATE "28-Oct-2026 09:15:00 +0300")'.encode()]

        self.bodies += 1
        parts = [b"1 (FLAGS (\\Seen))"] if self.unsolicited else []

        return "OK", [*parts, (f"1 (UID {uid} BODY[] {{{len(self.raw)}}}".encode(), self.raw), b")"]


@pytest.fixture
def mailbox(monkeypatch):
    def use(box: Mailbox) -> Mailbox:
        monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: box)
        monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)
        return box

    return use


LETTER = (
    "From: Бухгалтерия <buh@company.ru>\r\nTo: agent@company.ru\r\nCc: boss@company.ru\r\n"
    "Reply-To: pay@company.ru\r\nSubject: Счёт\r\nMessage-ID: <m2@company.ru>\r\nIn-Reply-To: <m1@company.ru>\r\n"
    "References: <m0@company.ru>\r\n <m1@company.ru>\r\n"
    + _mime("Content-Type: text/plain\r\n\r\nСчёт во вложении", "Content-Type: text/plain\r\nContent-Disposition: attachment; filename=счёт.txt\r\n\r\n42 300")
).encode()


def test_read_gives_what_a_reply_needs(mailbox, tmp_path):
    mailbox(Mailbox(LETTER, unsolicited=True))

    data = imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path)

    assert data["text"] == "Счёт во вложении"
    assert (data["message_id"], data["in_reply_to"], data["references"]) == ("<m2@company.ru>", "<m1@company.ru>", ["<m0@company.ru>", "<m1@company.ru>"])
    assert (data["cc"], data["reply_to"]) == ("boss@company.ru", "pay@company.ru")
    assert data["flags"] == ["seen", "answered", "$Важно"]
    assert [p.name for p in data["attachments"]] == ["счёт.txt"]


def test_reading_again_keeps_one_copy_of_a_file(mailbox, tmp_path):
    mailbox(Mailbox(LETTER))

    first = imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path)
    again = imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path)

    assert first["attachments"] == again["attachments"] and len(list(tmp_path.iterdir())) == 1


def test_a_letter_too_big_is_not_downloaded(mailbox, tmp_path):
    box = mailbox(Mailbox(LETTER, size=imap_smtp.MAX_LETTER_BYTES + 1))

    with pytest.raises(ToolError, match="больше 50 МБ"):
        imap_smtp.read({}, "", folder="INBOX", uid=7, save_dir=tmp_path)

    assert box.bodies == 0


async def test_a_long_text_is_read_in_windows(monkeypatch, tmp_path):
    from internal_tools.mail import read as tool
    from tests.test_mail import _sent_context

    monkeypatch.setattr(imap_smtp, "read", lambda *a, **k: {
        "uid": 7, "from": "a@b.ru", "from_email": "a@b.ru", "to": "", "cc": "", "reply_to": "", "subject": "s", "date": None,
        "message_id": None, "in_reply_to": None, "references": [], "flags": [], "text": "0123456789", "attachments": [],
    })
    ctx = _sent_context({})
    monkeypatch.setattr(type(ctx), "output_dir", lambda self, subdir="": tmp_path, raising=False)

    first = await tool.read(tool.Input(account="work", uid=7, max_chars=4), ctx)
    last = await tool.read(tool.Input(account="work", uid=7, offset=8, max_chars=4), ctx)

    assert (first.text, first.text_length, first.truncated) == ("0123", 10, True)
    assert (last.text, last.truncated) == ("89", False)


class Searching:
    def __init__(self, answer):
        self.answer = answer


    def select(self, folder, readonly=False):
        return "OK", [b"1"]


    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", self.answer

        raw = b"From: boss@company.ru\r\nSubject: Hi\r\n\r\n"
        # The UID after the headers, as some servers send it.
        return "OK", [(f"1 (BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)] {{{len(raw)}}}".encode(), raw), b" UID 5)"]


@pytest.mark.parametrize(("answer", "uids"), [([None], []), ([b""], []), ([b"5"], [5])])
def test_search_takes_the_answers_servers_give(monkeypatch, answer, uids):
    box = Searching(answer)
    monkeypatch.setattr(imap_smtp._imap, "__enter__", lambda self: box)
    monkeypatch.setattr(imap_smtp._imap, "__exit__", lambda self, *exc: None)

    found = imap_smtp.search({}, "", folder="INBOX", sender=None, subject=None, since=None, unseen=False, limit=10)

    assert [item["uid"] for item in found] == uids
