"""Accounts: the executor stores name, kind, settings and password; the kind's tool
group decides presets, required settings and how to try a login."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from common.errors import ApiError
from common.tools import discover
from common.tools.accounts import get_account_kind
from executor.api.accounts import AccountCreate, check_account, create_account


discover()  # tool groups register their account kinds on import


class _Secrets:
    def __init__(self):
        self.values: dict[str, str] = {}


    def set(self, ref, value):
        self.values[ref] = value


def _request(secrets=None, runtime=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(secrets=secrets or _Secrets(), runtime=runtime)))


def test_mail_registers_its_kind():
    kind = get_account_kind("imap_smtp")

    assert kind is not None and "gmail" in kind.presets and kind.check is not None


async def test_preset_fills_the_settings_and_the_password_goes_to_the_keyring(settings):
    secrets = _Secrets()
    body = AccountCreate(name="work", kind="imap_smtp", preset="yandex", config={"email": "me@yandex.ru"}, secret="app-pass")

    account = await create_account(body, _request(secrets))

    assert account["config"]["imap_host"] == "imap.yandex.ru"
    assert account["config"]["username"] == "me@yandex.ru"
    assert "secret" not in account and secrets.values == {"account:work": "app-pass"}


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
