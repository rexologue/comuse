"""Plugins as the stress test found them: a command's files written back as tools.show gives
them, a server whose process died between calls, a call cut off by a restart, accounts left
with their secrets after their plugin is gone, a new plugin's own account kind that could
not be tried, a form that took any value."""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from common.tools.accounts import get_account_kind
from executor import upstream as upstream_module
from executor.plugins import store
from executor.plugins.launch import Plugins, kind_of
from executor.plugins.manifest import PluginManifest
from executor.registry import ToolRegistry
from tests.test_plugin_accounts import MANIFEST as ACCOUNT_MANIFEST, WHOAMI
from tests.test_plugins import COUNT_LINES, HAVE_UV, TOOL
from tests.test_upstream import _upstream


needs_uv = pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")


@needs_uv
def test_files_written_back_as_tools_show_gives_them_are_the_same_files(settings):
    store.save_tool(settings.storage, "demo", "count_lines", entry={**TOOL}, files={"count_lines/main.py": COUNT_LINES}, requirements=[])

    files = store.view(settings.storage, "demo", "draft", files=True)["files"]

    assert "count_lines/main.py" in files and not any(path.startswith("count_lines/count_lines/") for path in files)


async def test_a_server_whose_process_died_meanwhile_is_started_anew(monkeypatch):
    monkeypatch.setattr(upstream_module, "_PING_AFTER_IDLE_SEC", 0)
    server = _upstream()
    await server.start()

    try:
        pid = (await server.call("pid", {})).structuredContent["result"]
        os.kill(int(pid), signal.SIGKILL)
        await asyncio.sleep(0.3)

        answer = await server.call("add", {"a": 1, "b": 2})

        assert answer.structuredContent == {"sum": 3}

    finally:
        await server.stop()


async def test_a_call_cut_off_by_a_stop_ends_at_once_not_at_its_timeout():
    from common.tools import ToolError

    server = _upstream()
    await server.start()

    async def stop_soon():
        await asyncio.sleep(0.5)
        await server.stop()

    started = time.monotonic()
    stopping = asyncio.create_task(stop_soon())

    with pytest.raises(ToolError, match="остановлен посреди вызова"):
        await server.call("slow", {"sec": 30})

    await stopping
    assert time.monotonic() - started < 10


@needs_uv
async def test_a_removed_plugin_takes_the_accounts_of_its_own_kind_with_it(settings, tmp_path):
    import httpx
    from fastapi import FastAPI

    from common.errors import install_error_handlers
    from common.secrets import SecretStore
    from common.storage.db import session
    from common.storage.models import Account
    from executor.api import plugins as plugins_api

    secrets = SecretStore("file", "test", tmp_path / "secrets.enc", "test-password")
    registry = ToolRegistry(settings.storage)
    registry.load()
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(plugins_api.router)
    app.state.settings, app.state.secrets, app.state.registry = settings, secrets, registry
    app.state.plugins = Plugins(registry, settings.storage)

    store.save_draft(settings.storage, "demo", manifest=ACCOUNT_MANIFEST, files={"main.py": WHOAMI}, requirements=[])
    secrets.set("account:mine", "secret")
    with session() as db:
        db.add(Account(id="acc_mine", name="mine", kind="demo_kind", config={"host": "h"}, secret_ref="account:mine"))
        db.add(Account(id="acc_mail", name="mail", kind="imap_smtp", config={}, secret_ref="account:mail"))
        db.commit()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://executor") as client:
        removed = await client.delete("/plugins/demo")
        missing = await client.delete("/plugins/demo")

    with session() as db:
        left = sorted(account.name for account in db.query(Account).all())

    assert removed.json()["accounts_deleted"] == ["mine"] and left == ["mail"]
    assert secrets.get("account:mine") is None
    assert missing.status_code == 404
    await registry.close()


@needs_uv
async def test_a_draft_plugins_own_account_kind_can_be_tried(settings):
    registry = ToolRegistry(settings.storage)
    registry.load()
    plugins = Plugins(registry, settings.storage)

    store.save_draft(settings.storage, "demo", manifest=ACCOUNT_MANIFEST, files={"main.py": WHOAMI}, requirements=[])
    plugins.offer_kind("demo")

    assert get_account_kind("demo_kind").title.endswith("(черновик плагина)")

    store.drop_draft(settings.storage, "demo")
    plugins.offer_kind("demo")

    assert get_account_kind("demo_kind") is None
    await registry.close()


@pytest.mark.parametrize(("config", "says"), [({"host": "h", "extra": 1}, "нет таких настроек"), ({"host": "h", "region": "mars"}, "нужно одно из")])
def test_an_accounts_form_takes_only_what_it_offers(config, says):
    manifest = {**ACCOUNT_MANIFEST, "account": {**ACCOUNT_MANIFEST["account"], "fields": [
        *ACCOUNT_MANIFEST["account"]["fields"], {"name": "region", "title": "Регион", "type": "choice", "choices": ["ru", "eu"]},
    ]}}
    kind = kind_of("demo", PluginManifest.model_validate(manifest))

    with pytest.raises(ValueError, match=says):
        kind.prepare(config)

    assert kind.prepare({"host": "h", "region": "eu"}) == {"host": "h", "region": "eu"}


@needs_uv
def test_a_failed_first_save_leaves_no_empty_folder(settings, monkeypatch):
    from common.tools import ToolError
    from executor.plugins import folder

    def unresolvable(*a, **k):
        raise ToolError("пакет не найден")

    monkeypatch.setattr(folder, "resolve", unresolvable)

    with pytest.raises(ToolError):
        store.save_draft(settings.storage, "fresh", manifest={"title": "x", "tools": [TOOL]}, files={"main.py": COUNT_LINES}, requirements=["nope-xyz"])

    assert not (settings.storage.plugins_dir / "fresh").exists()

