"""Settings changed through main's API reach every service on the next use; the
environment wins and locks a setting."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from common import tunables
from common.errors import install_error_handlers
from common.settings import SettingsError
from main.api import settings as settings_api


@pytest.fixture
async def client(settings):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(settings_api.router)
    app.state.settings = settings
    app.state.api_token = "test-api-token"

    headers = {"Authorization": "Bearer test-api-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://agent", headers=headers) as client:
        yield client


def _item(response, key: str) -> dict:
    return next(item for item in response.json()["settings"] if item["key"] == key)


async def test_a_change_applies_on_the_next_use(client):
    assert tunables.get("agent_max_iterations") == 16

    response = await client.patch("/settings", json={"agent_max_iterations": 5})

    assert response.status_code == 200
    assert _item(response, "agent_max_iterations") | {"description": ""} == {
        "key": "agent_max_iterations", "value": 5, "default": 16, "source": "db", "locked": False,
        "type": "int", "min": 1, "max": None, "choices": None, "description": "",
    }
    assert tunables.get("agent_max_iterations") == 5


async def test_null_returns_a_setting_to_its_default(client):
    await client.patch("/settings", json={"retention_days": 30})
    response = await client.patch("/settings", json={"retention_days": None})

    assert _item(response, "retention_days")["source"] == "default"
    assert tunables.get("retention_days") == 15


async def test_one_bad_value_changes_nothing(client):
    response = await client.patch("/settings", json={"retention_days": 30, "agent_tools_top_k": 0, "colour": "red"})

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"agent_tools_top_k": "не меньше 1", "colour": "нет такой настройки"}
    assert tunables.get("retention_days") == 15


async def test_the_environment_locks_a_setting(client, monkeypatch):
    monkeypatch.setenv("TOOL_TIMEOUT_SEC", "60")

    response = await client.patch("/settings", json={"tool_timeout_sec": 10})

    assert response.status_code == 400
    assert "TOOL_TIMEOUT_SEC" in response.json()["error"]["details"]["tool_timeout_sec"]
    assert _item(await client.get("/settings"), "tool_timeout_sec") | {"description": ""} == {
        "key": "tool_timeout_sec", "value": 60, "default": 300, "source": "env", "locked": True,
        "type": "int", "min": 1, "max": None, "choices": None, "description": "",
    }


async def test_the_workspace_is_a_full_path(client, monkeypatch, tmp_path):
    monkeypatch.delenv("WORKSPACE_DIR")

    response = await client.patch("/settings", json={"workspace_dir": "Documents"})
    assert response.json()["error"]["details"] == {
        "workspace_dir": "нужен полный путь к папке",
    }

    folder = tmp_path / "Документы"
    assert (await client.patch("/settings", json={"workspace_dir": str(folder)})).status_code == 200
    assert tunables.get("workspace_dir") == folder.resolve()


@pytest.mark.parametrize(
    ("key", "value"),
    [("RETENTION_DAYS", "two weeks"), ("LLM_CHAT_REASONING_EFFORT", "max"), ("LLM_NO_PROXY", "maybe")],
)
def test_a_bad_environment_value_stops_the_start(env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(SettingsError, match=key):
        tunables.check_env()


def test_a_comment_taken_for_a_value_stops_the_start(env, monkeypatch):
    """Docker's env_file turns `API_TOKEN= # empty = …` into the token "# empty = …"."""
    monkeypatch.setenv("API_TOKEN", "# empty = main makes one")

    with pytest.raises(SettingsError, match="API_TOKEN"):
        tunables.check_env()
