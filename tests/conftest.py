"""Shared fixtures: an isolated environment (temp data and workspace dirs, no network)
and a fake main client that plays the executor in memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from common.settings import Settings
from common.tunables import TUNABLES
from common.storage import migrate
from common.storage.db import init_engine


_ENV_KEYS = (
    "SERVICE_NAME", "PORT", "SELF_URL", "MAIN_URL", "SERVICE_HOST", "INTERNAL_TOKEN", "API_TOKEN", "ALLOWED_HOSTS", "ALLOWED_ORIGINS", "MAIN_PORT",
    "DATA_DIR", "LOG_DIR", "RUN_MODE", "WORKSPACE_DIR", "LLM_BASE_URL", "LLM_API_KEY", "LLM_NO_PROXY", "LLM_MODEL",
    "LLM_MODEL_CHAT", "LLM_MODEL_INSTRUCT", "LLM_MODEL_EMBED", "LLM_CHAT_REASONING_EFFORT",
    "LLM_EMBED_BASE_URL", "LLM_EMBED_API_KEY",
    "KEYRING_BACKEND", "KEYRING_PASSWORD",
    *(tunable.env for tunable in TUNABLES.values()),
)


@pytest.fixture
def env(monkeypatch, tmp_path: Path) -> Path:
    """Minimal valid environment rooted in tmp_path; returns that root."""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))

    (tmp_path / "workspace").mkdir()

    return tmp_path


@pytest.fixture
def settings(env) -> Settings:
    settings = Settings.from_env("runner")
    migrate.upgrade(init_engine(settings.storage), settings.storage)

    return settings


def tool_hash(name: str) -> str:
    """The hash FakeMain's catalog gives a tool."""
    return f"hash-{name}"


def kept_tools(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """TaskVersion.tools for a spec run against FakeMain: its tools as the catalog has them."""
    from pydantic import ValidationError

    from common.schemas.task import TaskSpec

    try:
        return {name: {"hash": tool_hash(name)} for name in TaskSpec.model_validate(spec).tools()}

    except ValidationError:
        return {}  # a broken spec: the runner refuses it before it looks at the tools


class FakeMain:
    """Stands in for MainClient: call_tool() goes to `tools` (each call is kept in
    `calls` as {tool, args, hash, context}), notifications and events are collected;
    tools() is the catalog (a tool's hash: tool_hash, or `hashes`). Any other call fails
    the test (a run never asks for anything)."""

    def __init__(self, tools: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]], *, hashes: dict[str, str] | None = None):
        from common.tools import discover

        self.handlers  = tools
        # The built-in tools and the faked ones, unchanged unless `hashes` says otherwise.
        self.hashes    = {name: tool_hash(name) for name in {*discover(), *tools}} | (hashes or {})
        self.calls: list[dict[str, Any]] = []
        self.notified: list[dict[str, Any]] = []
        self.changed: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []


    async def tools(self) -> dict[str, dict[str, Any]]:
        return {name: {"name": name, "hash": value} for name, value in self.hashes.items()}


    async def call_tool(self, tool: str, args: dict[str, Any], *, hash: str | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"tool": tool, "args": args, "hash": hash, "context": context or {}})
        if hash is not None and hash != self.hashes.get(tool):
            return {"status": "error", "error": "изменился", "tool_changed": True}

        return {"status": "ok", "artifacts": [], **self.handlers[tool](args, context or {})}


    async def post(self, path: str, json: Any = None, **_) -> Any:
        if path == "/internal/notify":
            self.notified.append(json)
            return {"delivered": True}

        if path == "/internal/tools-changed":
            self.changed.append(json)
            return {"paused": not json["dry_run"]}

        raise AssertionError(f"unexpected POST {path}")


    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))
