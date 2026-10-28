"""The executor's commands as an MCP server: `tools/list` is the catalog, `tools/call`
the same call pipeline as the chat and the runner use, built-in tools and own commands
alike: a client knows nothing of how a tool runs. How our manifests, calls and results
look in MCP (and what rides in `_meta`) is common/tools/mcp_codec.py."""

from __future__ import annotations

from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from pydantic import ValidationError

from common.schemas.execution import ExecuteRequest
from common.tools.mcp_codec import result_of, tool_of
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


NAME = "fintech2026-executor"


def build_server(registry: ToolRegistry, runtime: ExecutorRuntime) -> Server:
    server = Server(NAME)

    @server.list_tools()
    async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
        # The plugins not running ride along: their tools are missing for now, not gone.
        return types.ListToolsResult.model_validate({
            "tools": [tool_of(manifest) for manifest in registry.catalog()],
            "_meta": {"unavailable": dict(registry.unavailable)},
        })


    # The arguments are checked by the pipeline, with its own messages: not twice.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        meta = server.request_context.meta
        extra = (meta.model_extra or {}) if meta is not None else {}

        try:
            request = ExecuteRequest.model_validate({**extra, "tool": name, "args": arguments})

        except ValidationError as e:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Неверный _meta вызова: {e}")], isError=True)

        return result_of(await runtime.execute(request))


    return server
