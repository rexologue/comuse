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
def test_recipient_permissions_accept_addresses_and_domain_masks(entry):
    from common.schemas.task import Permissions

    assert Permissions(mail_send_to=[entry]).mail_send_to == [entry.strip().lower()]


@pytest.mark.parametrize("entry", ["*", "*@*", "*@", "boss", "a@b@c"])
def test_recipient_permissions_reject_too_broad_or_malformed(entry):
    from pydantic import ValidationError

    from common.schemas.task import Permissions

    with pytest.raises(ValidationError):
        Permissions(mail_send_to=[entry])


def test_domain_mask_allows_only_that_domain():
    from common.schemas.task import Permissions
    from common.tools import PermissionDenied, ToolContext

    ctx = ToolContext(
        mode="run", settings=None, paths=None, llm=None, get_account=None, publish=None,
        permissions=Permissions(mail_send_to=["*@company.local", "boss@other.ru"]),
    )

    ctx.check_recipients(["anyone@company.local", "Client@COMPANY.local", "boss@other.ru"])

    with pytest.raises(PermissionDenied, match="stranger@other.ru"):
        ctx.check_recipients(["anyone@company.local", "stranger@other.ru"])


def _mail(*uids):
    return [{"uid": uid} for uid in uids]


def test_first_poll_only_sets_the_cursor():
    from scheduler.jobs import split_new_mail

    assert split_new_mail({}, _mail(5, 7)) == ([], {"last_uid": 7})


def test_first_mail_in_an_empty_mailbox_triggers():
    from scheduler.jobs import split_new_mail

    fresh, state = split_new_mail({}, [])
    assert (fresh, state) == ([], {"last_uid": 0})

    fresh, state = split_new_mail(state, _mail(1))
    assert (fresh, state) == (_mail(1), {"last_uid": 1})


def test_only_mail_after_the_cursor_triggers():
    from scheduler.jobs import split_new_mail

    assert split_new_mail({"last_uid": 7}, _mail(9, 8, 7, 3)) == (_mail(9, 8), {"last_uid": 9})
    assert split_new_mail({"last_uid": 7}, []) == ([], {"last_uid": 7})
