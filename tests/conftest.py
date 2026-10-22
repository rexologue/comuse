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


class FakeMain:
    """Stands in for MainClient: `/executor/execute` goes to `tools`, notifications and
    events are collected. Any other call fails the test (a run never asks for anything)."""

    def __init__(self, tools: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]):
        self.tools     = tools
        self.calls: list[dict[str, Any]] = []
        self.notified: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []


    async def post(self, path: str, json: Any = None, **_) -> Any:
        if path == "/executor/execute":
            self.calls.append(json)
            handler = self.tools[json["tool"]]
            return {"status": "ok", "artifacts": [], **handler(json["args"], json["context"])}

        if path == "/internal/notify":
            self.notified.append(json)
            return {"delivered": True}

        raise AssertionError(f"unexpected POST {path}")


    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))
