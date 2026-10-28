"""The executor as an MCP server: the catalog through tools/list, the same call pipeline
through tools/call, what MCP has no field for in _meta; over Streamable HTTP behind the
internal token like any other executor route."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from fastapi import Depends, FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.memory import create_connected_server_and_client_session

from common.errors import install_error_handlers
from common.service import INTERNAL_TOKEN_HEADER, require_internal_token
from executor.api import mcp_http
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.tools.mcp_codec import manifest_of
from executor.mcp_server import build_server
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


@pytest.fixture
def runtime(settings):
    registry = ToolRegistry(settings.storage)
    registry.load()

    return ExecutorRuntime(settings, registry, secrets=None)


RUN = {"context": {"mode": "run", "run_id": "run_mcp", "step_key": "files"}}


async def test_the_catalog_is_the_tool_list_and_comes_back_whole(runtime):
    async with create_connected_server_and_client_session(build_server(runtime.registry, runtime)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == {manifest["name"] for manifest in runtime.registry.catalog()}

    # What MCP has no field for rides in _meta: the manifest comes back as the registry made it.
    assert manifest_of(tools["fs.list"]) == runtime.registry.manifest("fs.list")
    assert tools["fs.list"].meta["hash"] == runtime.registry.manifest("fs.list")["hash"]
    assert tools["fs.list"].annotations.readOnlyHint is True
    assert tools["mail.send"].annotations.readOnlyHint is False


async def test_a_call_goes_through_the_pipeline(runtime, env):
    (env / "workspace" / "a.txt").write_text("x", encoding="utf-8")
    good = runtime.registry.manifest("fs.list")["hash"]

    async with create_connected_server_and_client_session(build_server(runtime.registry, runtime)) as client:
        ok = await client.call_tool("fs.list", {"folder": "."}, meta={**RUN, "hash": good})
        stale = await client.call_tool("fs.list", {"folder": "."}, meta={**RUN, "hash": "another"})
        wrong = await client.call_tool("fs.list", {"no_such_argument": 1})
        outside = await client.call_tool("fs.list", {"folder": "../.."})

    assert not ok.isError and any(item["path"] == "a.txt" for item in ok.structuredContent["files"])
    assert ok.meta["duration_ms"] >= 0 and ok.meta["tool_changed"] is False

    assert stale.isError and stale.meta["tool_changed"] is True
    assert wrong.isError and "Неверные аргументы" in wrong.content[0].text
    assert outside.isError and "Нет доступа" in outside.content[0].text


async def test_what_a_tool_said_comes_back_in_meta(runtime, monkeypatch):
    from common.tools import ToolError

    async def fails(inp, ctx):
        ctx.log("прочитал 3 файла")
        ctx.notify("начинаю")
        raise ToolError("в четвёртом нет колонки «Сумма»")

    spec = replace(runtime.registry.get("data.map_rows"), fn=fails)
    monkeypatch.setattr(runtime.registry, "get", lambda name: spec if name == "data.map_rows" else None)

    async with create_connected_server_and_client_session(build_server(runtime.registry, runtime)) as client:
        result = await client.call_tool("data.map_rows", {"rows": [], "fields": {}}, meta=RUN)

    assert result.isError and result.content[0].text == "в четвёртом нет колонки «Сумма»"
    assert result.meta["logs"] == ["прочитал 3 файла"] and result.meta["notifications"] == ["начинаю"]


def _app(settings, runtime, prefix: str = "") -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(mcp_http.router, prefix=prefix, dependencies=[Depends(require_internal_token)])
    app.state.settings = replace(settings, service=replace(settings.service, internal_token="test-token"))
    app.state.registry = runtime.registry
    app.state.runtime = runtime

    return app


async def test_over_http_behind_the_internal_token(settings, runtime):
    app = _app(settings, runtime)

    async with mcp_http.serving(app):
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://executor") as anonymous:
            refused = await anonymous.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        assert refused.status_code == 401

        headers = {INTERNAL_TOKEN_HEADER: "test-token"}
        async with httpx.AsyncClient(transport=transport, base_url="http://executor", headers=headers) as http:
            async with streamable_http_client("http://executor/mcp", http_client=http) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    result = await client.call_tool("fs.list", {"folder": "."}, meta=RUN)

    assert "fs.list" in {tool.name for tool in tools}
    assert not result.isError and "files" in result.structuredContent


async def test_the_services_client_reads_and_calls_through_mcp(settings, runtime, env):
    """MainClient.tools() / call_tool(): what the chat, the runner and the compiler use."""
    (env / "workspace" / "a.txt").write_text("x", encoding="utf-8")
    app = _app(settings, runtime, prefix="/executor")   # as main proxies it

    async with mcp_http.serving(app):
        def client(token: str) -> MainClient:
            main = MainClient("http://main", internal_token=token)
            main._client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://main", headers={INTERNAL_TOKEN_HEADER: token})
            return main

        main = client("test-token")
        runtime.registry.unavailable["crm"] = "сервер завершился при запуске"
        tools = await main.tools()
        good = tools["fs.list"]["hash"]
        ok = await main.call_tool("fs.list", {"folder": "."}, hash=good, context=RUN["context"])
        stale = await main.call_tool("fs.list", {"folder": "."}, hash="another", context=RUN["context"])

        with pytest.raises(ApiError) as e:
            await client("forged").tools()

    assert tools["fs.list"] == runtime.registry.manifest("fs.list")
    # The plugins not running come with the catalog: their tools are missing, not gone.
    assert tools.unavailable == {"crm": "сервер завершился при запуске"}
    assert ok["status"] == "ok" and any(item["path"] == "a.txt" for item in ok["output"]["files"])
    assert stale["status"] == "error" and stale["tool_changed"] is True
    assert e.value.status_code == 401
