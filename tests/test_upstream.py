"""The aggregator: another MCP server, started by the executor over stdio, offers its
tools as `<group>.<tool>` next to the built-in ones, through the same pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp.shared.memory import create_connected_server_and_client_session

from common.schemas.execution import ExecContext, ExecuteRequest
from executor.mcp_server import build_server
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime
from executor.upstream import Upstream


SERVER = str(Path(__file__).with_name("mcp_demo_server.py"))


def _upstream(group: str = "demo", **env: str) -> Upstream:
    return Upstream(group, StdioServerParameters(command=sys.executable, args=["-I", SERVER], env=env or None))


@pytest.fixture
async def runtime(settings):
    registry = ToolRegistry(settings.storage)
    registry.load()
    await registry.connect(_upstream())

    yield ExecutorRuntime(settings, registry, secrets=None)

    await registry.close()


async def _call(runtime, tool, args, **request):
    return await runtime.execute(ExecuteRequest(tool=tool, args=args, context=ExecContext(mode="run", run_id="run_up"), **request))


async def test_its_tools_join_the_catalog_under_its_group(runtime):
    manifests = {manifest["name"]: manifest for manifest in runtime.registry.catalog()}

    assert {"demo.add", "demo.say_hi", "demo.fail", "fs.list"} <= set(manifests)
    add = manifests["demo.add"]
    assert add["source"] == "plugin" and add["confirm"] is True and add["hash"]
    assert add["output_schema"]["properties"]["sum"]["type"] == "integer"

    # A name functions do not allow (say-hi) is made one; a text answer is {"text": …}.
    assert manifests["demo.say_hi"]["output_schema"]["required"] == ["text"]

    # Over the executor's own MCP server they are tools like any other.
    async with create_connected_server_and_client_session(build_server(runtime.registry, runtime)) as client:
        assert "demo.add" in {tool.name for tool in (await client.list_tools()).tools}


async def test_a_call_goes_through_the_pipeline_to_the_server(runtime):
    added = await _call(runtime, "demo.add", {"a": 2, "b": 3}, hash=runtime.registry.manifest("demo.add")["hash"])
    hi = await _call(runtime, "demo.say_hi", {"name": "Анна"})
    wrong = await _call(runtime, "demo.add", {"a": "два"})
    failed = await _call(runtime, "demo.fail", {})
    stale = await _call(runtime, "demo.add", {"a": 1, "b": 1}, hash="another")

    assert (added.status, added.output) == ("ok", {"sum": 5})
    assert hi.output == {"text": "Привет, Анна"}
    assert wrong.status == "error" and "Неверные аргументы" in wrong.error
    assert failed.status == "error" and "сломалось" in failed.error
    assert stale.tool_changed


async def test_the_server_gets_none_of_the_executors_secrets(runtime, monkeypatch):
    # Set after the start would prove nothing: a new server is started with it in place.
    monkeypatch.setenv("INTERNAL_TOKEN", "do-not-leak")
    await runtime.registry.disconnect("demo")
    await runtime.registry.connect(_upstream(DEMO_ONLY="yes"))

    names = (await _call(runtime, "demo.env_names", {})).output["result"]

    assert "INTERNAL_TOKEN" not in names and "DEMO_ONLY" in names


async def test_a_server_that_went_away_is_started_again(runtime):
    died = await _call(runtime, "demo.die", {})
    again = await _call(runtime, "demo.add", {"a": 1, "b": 2})

    assert died.status == "error" and "закрыл соединение" in died.error
    assert again.output == {"sum": 3}


async def test_another_description_is_another_definition(settings):
    hashes = []

    for description in ("Складывает", "Складывает и отправляет результат куда-то ещё"):
        registry = ToolRegistry(settings.storage)
        await registry.connect(_upstream(DEMO_ADD_DESCRIPTION=description))
        hashes.append(registry.manifest("demo.add")["hash"])
        await registry.close()

    assert hashes[0] != hashes[1]


async def test_a_server_cannot_take_a_group_that_is_taken(settings):
    registry = ToolRegistry(settings.storage)
    await registry.connect(_upstream("fs"))

    try:
        assert registry.get("fs.add") is None and registry.get("fs.list") is not None
        # Nor can an own command take the server's group.
        await registry.connect(_upstream("demo"))
        assert "demo" in registry.builtin_groups()

    finally:
        await registry.close()
