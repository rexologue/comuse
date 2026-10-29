"""Accounts: the executor stores name, kind, settings and password; the kind's tool
group decides presets, required settings and how to try a login."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from common.errors import ApiError
from common.tools import discover
from common.tools.accounts import get_account_kind
from executor.api.accounts import AccountCreate, check_account, create_account, list_kinds


discover()  # tool groups register their account kinds on import


class _Secrets:
    def __init__(self):
        self.values: dict[str, str] = {}


    def set(self, ref, value):
        self.values[ref] = value


    def get(self, ref):
        return self.values.get(ref)


class _Plugins:
    """Hears which account kinds changed (plugins of the kind start again)."""

    def __init__(self):
        self.changed: list[str] = []

    async def account_changed(self, kind):
        self.changed.append(kind)


def _request(secrets=None, runtime=None):
    state = SimpleNamespace(secrets=secrets or _Secrets(), runtime=runtime, plugins=_Plugins())
    return SimpleNamespace(app=SimpleNamespace(state=state))


def test_mail_registers_its_kind():
    kind = get_account_kind("imap_smtp")

    assert kind is not None and "gmail" in kind.presets and kind.check is not None


async def test_a_kind_tells_a_form_what_to_ask():
    mail = (await list_kinds())["kinds"]["imap_smtp"]
    fields = {field["name"]: field for field in mail["fields"]}

    assert fields["email"]["required"] is True
    assert fields["imap_security"]["choices"] == ["ssl", "starttls", "none"]
    assert mail["secret_title"] == "Пароль приложения"
    # Every field a preset fills is one the form knows.
    assert all(key in fields for preset in mail["presets"].values() for key in preset)


async def test_preset_fills_the_settings_and_the_password_goes_to_the_keyring(settings):
    secrets = _Secrets()
    body = AccountCreate(name="work", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru"}, secret="app-pass")

    account = await create_account(body, _request(secrets))

    assert account["config"]["imap_host"] == "imap.yandex.ru"
    # No login stored: it falls back to the address, so editing the address moves it too.
    assert "username" not in account["config"]
    assert "secret" not in account and secrets.values == {"account:work": "app-pass"}


async def test_editing_without_a_password_keeps_the_old_one(settings):
    secrets = _Secrets()
    await create_account(AccountCreate(name="work", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru"}, secret="app-pass"), _request(secrets))

    edited = AccountCreate(name="work", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru", "smtp_security": "starttls"})
    account = await create_account(edited, _request(secrets))

    assert account["config"]["smtp_security"] == "starttls"
    assert secrets.values == {"account:work": "app-pass"}


async def test_a_new_account_needs_a_password(settings):
    with pytest.raises(ApiError) as e:
        await create_account(AccountCreate(name="new", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru"}), _request())

    assert e.value.status_code == 400 and "пароля" in e.value.message


async def test_a_password_does_not_go_to_another_kind(settings):
    """A mailbox renamed into a plugin's kind would hand the plugin the mail password."""
    from common.tools.accounts import AccountKind, account_kind, drop_account_kind

    account_kind(AccountKind(name="notes_kind", title="Заметки", owner="notes"))
    secrets, request = _Secrets(), _request()
    request.app.state.secrets = secrets
    await create_account(AccountCreate(name="work", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru"}, secret="mail-pass"), request)

    try:
        with pytest.raises(ApiError) as e:
            await create_account(AccountCreate(name="work", kind="notes_kind", config={}), request)
        assert e.value.status_code == 400 and "передайте его пароль" in e.value.message
        assert secrets.values == {"account:work": "mail-pass"}

        # With a password of its own it may: the mail one is gone, both kinds' plugins hear.
        await create_account(AccountCreate(name="work", kind="notes_kind", config={}, secret="notes-token"), request)
        assert secrets.values == {"account:work": "notes-token"}
        assert request.app.state.plugins.changed[-2:] == ["notes_kind", "imap_smtp"]

    finally:
        drop_account_kind("notes_kind", "notes")


@pytest.mark.parametrize(
    ("kind", "preset", "config"),
    [
        ("telegram", None, {}),                                   # no group offers this kind
        ("imap_smtp", "nope", {"email": "a@b.ru"}),               # unknown preset
        ("imap_smtp", None, {"email": "a@b.ru"}),                 # no servers
        ("imap_smtp", "gmail", {}),                               # no address
        ("imap_smtp", "gmail", {"email": "a@b.ru", "security": "tls13"}),
    ],
)
async def test_the_kind_refuses_incomplete_settings(settings, kind, preset, config):
    with pytest.raises(ApiError):
        await create_account(AccountCreate(name="w", kind=kind, preset=preset, config=config, secret="x"), _request())


async def test_check_uses_the_kinds_login(settings, monkeypatch):
    from common.tools import Credential
    from common.tools.accounts import AccountKind

    tried = []
    kind = AccountKind(name="imap_smtp", title="Почта", check=lambda config, secret: tried.append((config, secret)))
    monkeypatch.setattr("executor.api.accounts.get_account_kind", lambda name: kind)

    runtime = SimpleNamespace(load_credential=lambda name: Credential(name=name, kind="imap_smtp", config={"imap_host": "h"}, secret="s"))

    assert await check_account("work", _request(runtime=runtime)) == {"status": "ok"}
    assert tried == [({"imap_host": "h"}, "s")]


async def test_checking_an_unknown_account_is_not_found(settings):
    from executor.runtime import ExecutorRuntime

    runtime = ExecutorRuntime.__new__(ExecutorRuntime)

    with pytest.raises(ApiError) as error:
        await check_account("nope", _request(runtime=runtime))

    assert error.value.status_code == 404
