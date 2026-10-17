"""Shared fixtures: an isolated environment (temp data and workspace dirs, no network)
and a fake main client that plays executor and approvals in memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from common.settings import Settings
from common.storage.db import init_engine


_ENV_KEYS = (
    "SERVICE_NAME", "PORT", "SELF_URL", "MAIN_URL", "SERVICE_HOST", "INTERNAL_TOKEN", "API_TOKEN",
    "DATA_DIR", "WORKSPACE_DIR", "LLM_BASE_URL", "LLM_API_KEY", "LLM_NO_PROXY", "LLM_MODEL",
    "LLM_MODEL_CHAT", "LLM_MODEL_INSTRUCT", "LLM_MODEL_EMBED", "LLM_CHAT_REASONING_EFFORT",
    "KEYRING_BACKEND", "KEYRING_PASSWORD", "WATCH_POLLING",
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
    init_engine(settings.storage)

    return settings


class FakeMain:
    """Stands in for MainClient: `/executor/execute` goes to `tools`, approvals are
    recorded, events are collected."""

    def __init__(self, tools: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]):
        self.tools     = tools
        self.calls: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []


    async def post(self, path: str, json: Any = None, **_) -> Any:
        if path == "/executor/execute":
            self.calls.append(json)
            handler = self.tools[json["tool"]]
            return {"status": "ok", "artifacts": [], **handler(json["args"], json["context"])}

        if path == "/internal/approvals":
            approval = {"id": f"apr_{len(self.approvals) + 1}", **json}
            self.approvals.append(approval)
            return approval

        raise AssertionError(f"unexpected POST {path}")


    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append((event_type, payload))
