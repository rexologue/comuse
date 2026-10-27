"""The executor's commands as an MCP server: `tools/list` is the catalog, `tools/call`
the same call pipeline as /execute (executor/runtime.py). Built-in tools and own
commands alike; a client knows nothing of how a tool runs.

What MCP has no field for travels in `_meta`, the protocol's place for it:

- a tool's `_meta`: the rest of its manifest (hash, confirm, card, settings_schema,
  source, …), so a client gets back exactly what GET /tools gives;
- a call's `_meta`: what ExecuteRequest carries besides the tool and its arguments —
  `hash` (the definition the task was approved with) and `context` (mode, run_id,
  step_key, tool_settings, dry_run…);
- a result's `_meta`: what ExecuteResponse carries besides the output — logs,
  notifications, artifacts, duration, traceback, timed_out, tool_changed.

A client that does not know these (any MCP client) gets plain tools: the schemas, the
output as structuredContent and as text, an error as isError."""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from pydantic import ValidationError

from common.schemas.execution import ExecuteRequest
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


NAME = "fintech2026-executor"

# Manifest fields MCP has a field of its own for; the rest goes to the tool's _meta.
_OWN_FIELDS = {"name": "name", "title": "title", "description": "description", "input_schema": "inputSchema", "output_schema": "outputSchema"}


def build_server(registry: ToolRegistry, runtime: ExecutorRuntime) -> Server:
    server = Server(NAME)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [tool_of(manifest) for manifest in registry.catalog()]


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


def tool_of(manifest: dict[str, Any]) -> types.Tool:
    """A manifest as an MCP tool; manifest_of() is the way back."""
    return types.Tool(
        **{field: manifest.get(key) for key, field in _OWN_FIELDS.items()},
        # A command that never asks (confirm: false) only looks or computes.
        annotations=types.ToolAnnotations(readOnlyHint=manifest.get("confirm") is False),
        _meta={key: value for key, value in manifest.items() if key not in _OWN_FIELDS},
    )


def manifest_of(tool: types.Tool) -> dict[str, Any]:
    return {**(tool.meta or {}), **{key: getattr(tool, field) for key, field in _OWN_FIELDS.items()}}


def result_of(response) -> types.CallToolResult:
    """ExecuteResponse → CallToolResult: the output (or the error) and the rest in _meta."""
    ok = response.status == "ok"
    text = json.dumps(response.output, ensure_ascii=False) if ok else (response.error or "Ошибка")

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=response.output if ok and isinstance(response.output, dict) else None,
        isError=not ok,
        _meta=response.model_dump(exclude={"status", "output", "error"}),
    )
