"""Our tools and calls in MCP's shapes and back: the executor's server writes them
(executor/mcp_server.py), the services' client reads them (MainClient.tools,
MainClient.call_tool).

What MCP has no field for travels in `_meta`, the protocol's place for it:

- a tool's `_meta`: the rest of its manifest (hash, confirm, card, settings_schema,
  source, …), so a client gets back exactly the manifest the registry made;
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

from common.schemas.execution import ExecuteResponse


# Manifest fields MCP has a field of its own for; the rest goes to the tool's _meta.
_OWN_FIELDS = {"name": "name", "title": "title", "description": "description", "input_schema": "inputSchema", "output_schema": "outputSchema"}


def tool_of(manifest: dict[str, Any]) -> types.Tool:
    return types.Tool(
        **{field: manifest.get(key) for key, field in _OWN_FIELDS.items()},
        # A command that never asks (confirm: false) only looks or computes.
        annotations=types.ToolAnnotations(readOnlyHint=manifest.get("confirm") is False),
        _meta={key: value for key, value in manifest.items() if key not in _OWN_FIELDS},
    )


def manifest_of(tool: types.Tool) -> dict[str, Any]:
    return {**(tool.meta or {}), **{key: getattr(tool, field) for key, field in _OWN_FIELDS.items()}}


def call_meta(*, hash: str | None, context: dict[str, Any] | None) -> dict[str, Any]:
    return {"hash": hash, "context": context or {}}


def result_of(response: ExecuteResponse) -> types.CallToolResult:
    ok = response.status == "ok"
    text = json.dumps(response.output, ensure_ascii=False) if ok else (response.error or "Ошибка")

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structuredContent=response.output if ok and isinstance(response.output, dict) else None,
        isError=not ok,
        _meta=response.model_dump(exclude={"status", "output", "error"}),
    )


def response_of(result: types.CallToolResult) -> ExecuteResponse:
    """A result back as ExecuteResponse: the output from structuredContent (or the text,
    as JSON if it is), the error from the text."""
    text = "\n".join(item.text for item in result.content if isinstance(item, types.TextContent))
    meta = result.meta or {}
    fields = {key: meta[key] for key in ExecuteResponse.model_fields if key in meta and key not in ("status", "output", "error")}

    if result.isError:
        return ExecuteResponse(status="error", error=text or "Ошибка", **fields)

    output = result.structuredContent
    if output is None:
        try:
            output = json.loads(text)

        except ValueError:
            output = {"text": text}

    return ExecuteResponse(status="ok", output=output, **fields)
