"""Mail adapter: connection security selection, and a live round trip against a
local GreenMail (skipped when it is not running on 127.0.0.1:3025/3143)."""

from __future__ import annotations

import socket
import uuid
from datetime import date

import pytest

from common.tools import ToolError
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.adapters.presets import PRESETS


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


def _greenmail_up() -> bool:
    for port in (3025, 3143):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()

        except OSError:
            return False

    return True


live = pytest.mark.skipif(not _greenmail_up(), reason="GreenMail is not running on 127.0.0.1")


@live
def test_round_trip_against_greenmail(tmp_path):
    # A fresh mailbox per run: GreenMail creates it on first login.
    box = f"boss-{uuid.uuid4().hex[:8]}@test.local"
    sender = {**PRESETS["greenmail"], "email": "agent@test.local"}
    receiver = {**PRESETS["greenmail"], "email": box}

    attachment = tmp_path / "Отчёт.txt"
    attachment.write_text("итоги недели", encoding="utf-8")

    message = imap_smtp.build_message(
        sender, to=[box], cc=[], subject="Еженедельный отчёт", body="Отчёт во вложении.", attachments=[attachment]
    )
    imap_smtp.send(sender, "x", message)

    found = imap_smtp.search(
        receiver, "x", folder="INBOX", sender=None, subject="отчёт", since=date.today(), unseen=False, limit=10
    )
    assert [m["subject"] for m in found] == ["Еженедельный отчёт"]
    assert found[0]["from"] == "agent@test.local"

    mail = imap_smtp.read(receiver, "x", folder="INBOX", uid=found[0]["uid"], save_dir=tmp_path / "in")
    assert mail["text"].strip() == "Отчёт во вложении."
    assert [p.name for p in mail["attachments"]] == ["Отчёт.txt"]
    assert mail["attachments"][0].read_text(encoding="utf-8") == "итоги недели"

    # The test server starts with INBOX only: the adapter creates Drafts on demand.
    draft = imap_smtp.build_message(receiver, to=["x@test.local"], cc=[], subject="Черновик", body="…", attachments=[])
    assert imap_smtp.append_draft(receiver, "x", draft) == "Drafts"
    assert [m["subject"] for m in imap_smtp.search(
        receiver, "x", folder="Drafts", sender=None, subject=None, since=None, unseen=False, limit=10
    )] == ["Черновик"]
