"""The MCP server of a plugin of functions: runs inside the plugin's own .venv and
serves the functions its plugin.json lists. Standard library only: the plugin's .venv
has its own packages and none of ours, so this speaks the little of MCP it needs —
JSON-RPC lines over stdio: initialize, tools/list, tools/call, ping.

    python -I -B -X utf8 shim.py <plugin dir>

A function is `def run(args, ctx)` returning a JSON object. `ctx` has mode, dry_run,
output_dir, run_id, step_key, session_id, log(text), notify(text), output_path(filename);
raising agent_tool.ToolError reports a plain error to the user. The context comes from
the call's `_meta` ("context"); what the function logged, told the user and made comes
back in the result's `_meta` (logs, notifications, artifacts): the executor knows what
to do with them.

Calls run in threads of their own, so a slow one does not hold the others; a call the
executor gave up on is not interrupted here — the executor stops the whole process.
stdout is the protocol's: whatever a function prints goes to stderr, which the
executor does not keep."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import traceback
import types
from pathlib import Path
from typing import Any


# What we answer when the client asks for a version we do not know.
PROTOCOL_VERSION = "2025-06-18"


class ToolError(Exception):
    """An error for the user, without a traceback."""


class Context:
    def __init__(self, data: dict[str, Any]):
        self.mode = data.get("mode", "chat")
        self.dry_run = bool(data.get("dry_run"))
        self.run_id = data.get("run_id")
        self.step_key = data.get("step_key")
        self.session_id = data.get("session_id")
        self.output_dir = Path(data["output_dir"]) if data.get("output_dir") else None
        self.logs: list[str] = []
        self.notifications: list[str] = []
        self.artifacts: list[str] = []

    def log(self, message: Any) -> None:
        self.logs.append(str(message))

    def notify(self, message: Any) -> None:
        """A message for the user: it goes to the task's chat."""
        self.notifications.append(str(message))

    def output_path(self, filename: str) -> Path:
        """A file in the run's results, recorded as one of its artifacts."""
        if self.output_dir is None:
            raise ToolError("Файлам результата здесь некуда лечь")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / (Path(filename).name or "output")
        self.artifacts.append(str(path))
        return path


class Plugin:
    def __init__(self, folder: Path):
        self.folder = folder
        self.manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
        self.entries = {entry["name"]: entry for entry in self.manifest["tools"]}
        self._functions: dict[str, Any] = {}
        self._loading = threading.Lock()


    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "title": entry["title"],
                "description": entry["description"],
                "inputSchema": entry.get("input_schema") or {"type": "object", "properties": {}},
                "outputSchema": entry.get("output_schema") or {"type": "object"},
            }
            for name, entry in self.entries.items()
        ]


    def call(self, name: str, arguments: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        ctx = Context(context)

        if name not in self.entries:
            return _error(f"Нет инструмента {name}", ctx)

        try:
            output = self._function(name)(arguments, ctx)
            # What json cannot write (a date, a path) goes as text, as it did before.
            output = json.loads(json.dumps(output, ensure_ascii=False, default=str))

        except ToolError as e:
            return _error(str(e), ctx)

        except BaseException as e:
            return _error(f"{type(e).__name__}: {e}", ctx, traceback.format_exc())

        if not isinstance(output, dict):
            return _error(f"run вернула {type(output).__name__}, а нужен объект (dict)", ctx)

        return {
            "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
            "structuredContent": output,
            "isError": False,
            "_meta": {"logs": ctx.logs, "notifications": ctx.notifications, "artifacts": ctx.artifacts},
        }


    def _function(self, name: str):
        with self._loading:
            if name not in self._functions:
                path = self.folder / self.entries[name]["entry"]
                # Its sibling modules import as they did when it was a command of its own.
                if str(path.parent) not in sys.path:
                    sys.path.insert(0, str(path.parent))

                spec = importlib.util.spec_from_file_location(f"plugin_tool_{name}", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._functions[name] = module.run

            return self._functions[name]


def _error(text: str, ctx: Context, trace: str | None = None) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": True,
        "_meta": {"logs": ctx.logs, "notifications": ctx.notifications, "traceback": trace},
    }


class Server:
    def __init__(self, plugin: Plugin, out: io.TextIOBase):
        self.plugin = plugin
        self.out = out
        self._writing = threading.Lock()


    def handle(self, message: dict[str, Any]) -> None:
        method, params, request_id = message.get("method"), message.get("params") or {}, message.get("id")

        # A notification (initialized, cancelled…) needs no answer; a response is not ours to read.
        if request_id is None or method is None:
            return

        if method == "initialize":
            self.reply(request_id, {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.plugin.manifest.get("title") or self.plugin.folder.parent.name, "version": "1"},
            })

        elif method == "ping":
            self.reply(request_id, {})

        elif method == "tools/list":
            self.reply(request_id, {"tools": self.plugin.tools()})

        elif method == "tools/call":
            context = (params.get("_meta") or {}).get("context") or {}
            work = lambda: self.reply(request_id, self.plugin.call(params.get("name"), params.get("arguments") or {}, context))
            threading.Thread(target=work, daemon=True).start()

        else:
            self.send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})


    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})


    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False)

        with self._writing:
            self.out.write(line + "\n")
            self.out.flush()


def main() -> None:
    folder = Path(sys.argv[1]).resolve()

    # `from agent_tool import ToolError`, as commands wrote it before plugins.
    module = types.ModuleType("agent_tool")
    module.ToolError = ToolError
    module.Context = Context
    sys.modules["agent_tool"] = module

    # The protocol's stdout, before a function's print() is sent elsewhere.
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stdout = sys.stderr
    server = Server(Plugin(folder), out)

    for line in io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"):
        if not line.strip():
            continue

        try:
            server.handle(json.loads(line))

        except ValueError:
            server.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})


if __name__ == "__main__":
    main()
