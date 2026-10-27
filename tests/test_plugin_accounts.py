"""A plugin that works for accounts: a server per account, the account's settings and
secret only in that process's environment, our `account` argument in place of the
server's own. (A plugin of functions here: its function shows what its process got.)"""

from __future__ import annotations

import pytest

from common.schemas.execution import ExecContext, ExecuteRequest
from common.secrets import SecretStore
from common.storage.db import session
from common.storage.models import Account
from common.tools.accounts import get_account_kind
from executor.plugins import store
from executor.plugins.launch import Plugins, first_account
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime
from tests.test_plugins import HAVE_UV


pytestmark = pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")

WHOAMI = '''
import os


def run(args, ctx):
    return {
        "account": args["account_name"],
        "host": os.environ.get("DEMO_HOST"),
        "has_password": bool(os.environ.get("DEMO_PASSWORD")),
        "pid": os.getpid(),
        "home": os.environ.get("HOME"),
    }
'''

MANIFEST = {
    "title": "Демо с аккаунтами",
    "tools": [{
        "name": "whoami",
        "title": "Кто я",
        "description": "Что знает процесс аккаунта",
        "confirm": False,
        "input_schema": {"type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"]},
        "output_schema": {"type": "object"},
    }],
    "account": {
        "kind": "demo_kind",
        "title": "Демо-сервис",
        "fields": [{"name": "host", "title": "Сервер", "required": True}],
        "secret_title": "Токен",
        "env": {"DEMO_HOST": "${account.host}", "DEMO_PASSWORD": "${account.secret}"},
        "argument": "account_name",
    },
}


@pytest.fixture
async def setup(settings, tmp_path):
    secrets = SecretStore("file", "test", tmp_path / "secrets.enc", "test-password")
    registry = ToolRegistry(settings.storage)
    registry.load()
    runtime = ExecutorRuntime(settings, registry, secrets)
    plugins = Plugins(registry, settings.storage, first_account(runtime.load_credential))

    def account(name: str, kind: str = "demo_kind", **config):
        secrets.set(f"account:{name}", f"secret-of-{name}")
        with session() as db:
            row = db.query(Account).filter(Account.name == name).one_or_none() or Account(id=f"acc_{name}", name=name)
            row.kind, row.config, row.secret_ref = kind, config, f"account:{name}"
            db.add(row)
            db.commit()

    draft = store.save_draft(settings.storage, "demo", manifest=MANIFEST, files={"main.py": WHOAMI}, requirements=[])
    store.approve(settings.storage, "demo", draft["hash"])
    await plugins.start("demo")

    yield runtime, plugins, account

    await registry.close()


async def _whoami(runtime, account: str | None):
    args = {} if account is None else {"account": account}
    return await runtime.execute(ExecuteRequest(tool="demo.whoami", args=args, context=ExecContext(mode="chat", session_id="ses_acc")))


async def test_the_model_names_our_account_not_the_servers_argument(setup):
    runtime, plugins, _ = setup
    manifest = runtime.registry.manifest("demo.whoami")

    assert set(manifest["input_schema"]["properties"]) == {"account"}
    assert manifest["input_schema"]["required"] == ["account"] and manifest["secrets"] == ["account"]

    # Its own kind is there for new accounts while the plugin is approved.
    kind = get_account_kind("demo_kind")
    assert kind.owner == "demo" and [field.name for field in kind.fields] == ["host"] and kind.secret_title == "Токен"

    await plugins.stop("demo")
    assert get_account_kind("demo_kind") is None


async def test_each_account_has_a_process_of_its_own(setup):
    runtime, _, account = setup
    account("work", host="imap.work.test")
    account("home", host="imap.home.test")

    work, home = await _whoami(runtime, "work"), await _whoami(runtime, "home")

    assert work.output["account"] == "work" and work.output["host"] == "imap.work.test" and work.output["has_password"]
    assert home.output["host"] == "imap.home.test"
    assert work.output["pid"] != home.output["pid"]
    # A home of the plugin's own: what a server keeps stays with the plugin.
    assert work.output["home"].endswith("/plugins/demo/home") and work.output["home"] == home.output["home"]
    # The secret went into the process's environment, not anywhere a log keeps.
    assert "secret-of-work" not in str(work.model_dump())

    # Same account again: the same process.
    assert (await _whoami(runtime, "work")).output["pid"] == work.output["pid"]


async def test_changed_settings_make_a_new_process(setup):
    runtime, _, account = setup
    account("work", host="old.test")
    before = await _whoami(runtime, "work")

    account("work", host="new.test")
    after = await _whoami(runtime, "work")

    assert after.output["host"] == "new.test" and after.output["pid"] != before.output["pid"]


async def test_the_account_must_be_there_and_of_the_plugins_kind(setup):
    runtime, _, account = setup
    account("mailbox", kind="imap_smtp", email="a@b.test")

    assert "Неверные аргументы" in (await _whoami(runtime, None)).error
    assert "не настроен" in (await _whoami(runtime, "nobody")).error
    assert "нужен вид demo_kind" in (await _whoami(runtime, "mailbox")).error


async def test_the_catalog_server_takes_the_first_account(setup):
    """Accounts made later reach the catalog's server by a restart of the plugin."""
    runtime, plugins, account = setup
    account("alpha", host="alpha.test")
    await plugins.account_changed("demo_kind")

    upstream = runtime.registry.upstreams["demo"]
    assert upstream.account_key.startswith("alpha:")
    # The first account's calls go to that same server.
    assert upstream.for_account(runtime.load_credential("alpha")) is upstream


def test_a_plugin_cannot_take_an_account_kind_that_is_anothers(settings):
    import internal_tools  # noqa: F401  (the built-in kinds register on import)
    from common.tools import discover

    discover()
    manifest = {**MANIFEST, "account": {**MANIFEST["account"], "kind": "imap_smtp"}}

    with pytest.raises(Exception, match="этот вид аккаунта уже есть"):
        store.save_draft(settings.storage, "other", manifest=manifest, files={"main.py": WHOAMI}, requirements=[])

    # Using a built-in kind as it is (no fields of its own) is fine.
    plain = {**MANIFEST, "account": {k: v for k, v in MANIFEST["account"].items() if k not in ("fields", "title", "secret_title")} | {"kind": "imap_smtp"}}
    assert store.save_draft(settings.storage, "other", manifest=plain, files={"main.py": WHOAMI}, requirements=[])["status"] == "draft"


def test_placeholders_compare_and_an_empty_variable_is_not_set():
    from common.tools import Credential
    from executor.plugins.manifest import AccountSpec, account_env

    spec = AccountSpec(kind="imap_smtp", env={
        "SSL": "${account.imap_security|ssl=ssl}", "STARTTLS": "${account.imap_security|ssl=starttls}",
        "USER": "${account.username}", "PORT": "${account.imap_port|993}",
    })

    plain = account_env(spec, Credential(name="w", kind="imap_smtp", config={"imap_security": "none", "username": None}, secret="s"))
    default = account_env(spec, Credential(name="w", kind="imap_smtp", config={}, secret="s"))

    assert plain == {"SSL": "false", "STARTTLS": "false", "PORT": "993"}
    assert default == {"SSL": "true", "STARTTLS": "false", "PORT": "993"}
