"""Runs one call of an external tool inside the tool's own .venv (stdlib only: the tool's
environment has none of our packages).

    python -I shim.py <version dir>     request on stdin, answer on stdout

Request: {"args": {...}, "context": {"mode", "dry_run", "output_dir", ...}}.
Answer:  {"ok": true, "output": {...}, "logs": [...], "artifacts": [...]}
      or {"ok": false, "error": "...", "traceback": "...", "logs": [...]}.

The tool's main.py defines `run(args, ctx)` returning a JSON object. `ctx` has mode,
dry_run, output_dir, log(text), output_path(filename); raising agent_tool.ToolError
reports a plain error to the user. Whatever the tool prints goes to stderr, so it
cannot break the answer."""

import io
import json
import os
import sys
import traceback
import types
from pathlib import Path


class ToolError(Exception):
    """An error for the user, without a traceback."""


class Context:
    def __init__(self, data):
        self.mode = data.get("mode", "chat")
        self.dry_run = bool(data.get("dry_run"))
        self.output_dir = Path(data["output_dir"])
        self.logs = []
        self.artifacts = []

    def log(self, message):
        self.logs.append(str(message))

    def output_path(self, filename):
        """A file in the run's results, recorded as one of its artifacts."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / (Path(filename).name or "output")
        self.artifacts.append(str(path))
        return path


def main():
    version_dir = Path(sys.argv[1]).resolve()
    answer_to = sys.stdout
    sys.stdout = sys.stderr

    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    ctx = Context(request.get("context") or {})

    module = types.ModuleType("agent_tool")
    module.ToolError = ToolError
    module.Context = Context
    sys.modules["agent_tool"] = module

    sys.path.insert(0, str(version_dir))
    os.chdir(ctx.output_dir if ctx.output_dir.is_dir() else version_dir)

    try:
        import main as tool

        output = tool.run(request.get("args") or {}, ctx)
        answer = {"ok": True, "output": output, "logs": ctx.logs, "artifacts": ctx.artifacts}

    except ToolError as e:
        answer = {"ok": False, "error": str(e), "logs": ctx.logs}

    except BaseException as e:
        answer = {"ok": False, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc(), "logs": ctx.logs}

    try:
        text = json.dumps(answer, ensure_ascii=False, default=str)

    except (TypeError, ValueError) as e:
        text = json.dumps({"ok": False, "error": f"Результат не переводится в JSON: {e}", "logs": ctx.logs})

    out = io.TextIOWrapper(answer_to.buffer, encoding="utf-8")
    out.write(text)
    out.flush()


if __name__ == "__main__":
    main()
