"""External tools: commands the user adds or the chat writes, outside internal_tools/.

A command is `ext.<name>`; each version is a folder

    DATA_DIR/external_tools/<name>/<version>/
        tool.json           manifest (manifest.py): title, description, schemas, …
        main.py             `def run(args, ctx)`, plus any other .py files
        requirements.txt    the packages it declares
        requirements.lock   the exact packages with hashes (uv pip compile)
        .venv/              its own environment, never part of the version's hash

and a row of `external_tool_versions`. A version starts as the command's one draft,
changed in place until a person approves it; then its files are frozen (their hash is
checked before every run) and it becomes the command's current version. The executor
is the only writer of the tables and the folders.

A version runs in a process of its own (process.py) in its own .venv: it gets its
arguments and context as JSON and answers in JSON, sees no tokens or keys, and is
killed at its timeout. That is not a sandbox: the code can read what the user can.
What makes it safe to run is the person who read and approved it."""

from __future__ import annotations

import re
from typing import Any

from common.schemas.task import tool_steps


PREFIX = "ext."

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def full_name(name: str) -> str:
    return PREFIX + name


def is_external(tool: str) -> bool:
    return tool.startswith(PREFIX)


def pinned(spec: dict[str, Any]) -> dict[str, int]:
    """{tool: version} the external steps of a task spec run (nested steps too)."""
    found: dict[str, int] = {}

    for step in tool_steps(spec):
        if is_external(step["tool"]) and isinstance(step.get("tool_version"), int):
            found.setdefault(step["tool"], step["tool_version"])

    return found
