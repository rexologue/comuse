"""A provider is one model at a server; roles point at any provider and are checked
before they change. LLM_* variables become ordinary providers on main's start."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agent_loop.loop import AgentLoop
from common.errors import install_error_handlers
from common.llm import LLM, LLMNotConfigured
from common.settings import Settings
from common.storage.db import session
from common.storage.models import LLMProvider
from main import providers
from main.api import llm as llm_api


# The real role check, before the fixture below replaces it.
REAL_CHECK = providers.check

@pytest.fixture
def main_settings(settings, monkeypatch) -> Settings:
    """Keys go to an encrypted file instead of the system keyring."""
    monkeypatch.setenv("KEYRING_BACKEND", "file")
    monkeypatch.setenv("KEYRING_PASSWORD", "test-only")

    return Settings.from_env("main")


@pytest.fixture(autouse=True)
def checks(monkeypatch):
    """No network: a model named `plain` cannot call tools, every other one passes."""
    async def check(client, model, role):
        if role == "chat" and model == "plain":
            return {"ok": False, "error": "модель ответила текстом, а не вызовом функции"}

        return {"ok": True}

    monkeypatch.setattr(providers, "check", check)


@pytest.fixture
async def client(main_settings):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(llm_api.router)
    app.state.settings = main_settings
    app.state.api_token = "test-api-token"

    headers = {"Authorization": "Bearer test-api-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://agent", headers=headers) as client:
        yield client


async def _add(client, name: str, model: str = "big", **fields) -> str:
    response = await client.post("/llm/providers", json={"name": name, "base_url": "http://llm-host:8000/v1", "model": model, **fields})
    assert response.status_code == 200, response.text

    return response.json()["id"]


async def test_nothing_serves_a_role_until_a_provider_is_set_for_it(client, main_settings):
    await _add(client, "Сервер")

    assert (await client.get("/llm")).json()["roles"] == {"chat": None, "instruct": None, "embed": None}

    with pytest.raises(LLMNotConfigured):
        await LLM(main_settings).chat.complete([{"role": "user", "content": "?"}])


async def test_any_provider_serves_any_role_and_instruct_follows_chat(client, main_settings):
    llm = LLM(main_settings)
    big = await _add(client, "Большая", "big")
    fast = await _add(client, "Быстрая", "fast")
    bge = await _add(client, "Эмбеддинги", "bge-m3")

    await client.patch("/llm", json={"chat": big, "embed": bge})
    roles = (await client.get("/llm")).json()["roles"]

    assert roles["chat"] == {"provider": big, "name": "Большая", "model": "big"}
    assert roles["instruct"] == {"provider": big, "name": "Большая", "model": "big", "follows_chat": True}
    assert (llm.chat.endpoint().model, llm.instruct.endpoint().model, llm.embed.endpoint().model) == ("big", "big", "bge-m3")

    await client.patch("/llm", json={"instruct": fast})
    assert llm.instruct.endpoint().model == "fast"

    await client.patch("/llm", json={"instruct": None})
    assert llm.instruct.endpoint().model == "big"


async def test_a_role_changes_only_when_its_check_passes(client):
    plain = await _add(client, "Без вызовов", "plain")
    bge = await _add(client, "Эмбеддинги", "bge-m3")

    response = await client.patch("/llm", json={"chat": plain, "embed": bge})

    assert response.status_code == 400
    assert "chat" in response.json()["error"]["details"]
    assert (await client.get("/llm")).json()["roles"]["embed"] is None


async def test_the_key_is_never_shown_and_a_new_one_reaches_the_services(client, main_settings):
    llm = LLM(main_settings)
    provider = await _add(client, "Облако", api_key="sk-first")
    await client.patch("/llm", json={"chat": provider})

    assert "sk-first" not in (await client.get("/llm")).text
    assert llm.client(llm.chat.endpoint()).api_key == "sk-first"

    await client.patch(f"/llm/providers/{provider}", json={"api_key": "sk-second"})
    assert llm.client(llm.chat.endpoint()).api_key == "sk-second"

    removed = await client.patch(f"/llm/providers/{provider}", json={"api_key": None})
    assert removed.json()["has_key"] is False
    assert llm.client(llm.chat.endpoint()).api_key == ""


async def test_a_provider_a_role_uses_cannot_be_removed(client):
    used = await _add(client, "Нужный")
    spare = await _add(client, "Запасной")
    await client.patch("/llm", json={"chat": used})

    assert (await client.delete(f"/llm/providers/{used}")).status_code == 409
    assert (await client.delete(f"/llm/providers/{spare}")).status_code == 200


async def test_bad_provider_fields_are_refused(client):
    await _add(client, "Сервер")

    duplicate = await client.post("/llm/providers", json={"name": "Сервер", "base_url": "http://other/v1", "model": "m"})
    no_scheme = await client.post("/llm/providers", json={"name": "Ещё", "base_url": "llm-host:8000/v1", "model": "m"})

    assert duplicate.status_code == 409
    assert no_scheme.status_code == 422


def _providers() -> dict[str, str]:
    with session() as db:
        return {p.name: p.model for p in db.query(LLMProvider)}


async def test_llm_variables_become_ordinary_providers_once(client, main_settings, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_EMBED", "bge-m3")
    monkeypatch.setenv("LLM_API_KEY", "sk-env")
    settings = Settings.from_env("main")

    providers.register_env_models(settings)
    overview = (await client.get("/llm")).json()

    assert _providers() == {"default": "test-model", "default-embed": "bge-m3"}
    assert overview["roles"]["chat"]["name"] == "default"
    assert overview["roles"]["embed"]["name"] == "default-embed"
    assert LLM(settings).client(LLM(settings).chat.endpoint()).api_key == "sk-env"

    # Renamed and moved off the role, it stays the user's choice on the next start.
    default = overview["roles"]["chat"]["provider"]
    other = await _add(client, "Другая", "other")
    await client.patch(f"/llm/providers/{default}", json={"name": "Сервер команды"})
    await client.patch("/llm", json={"chat": other})

    providers.register_env_models(settings)

    assert _providers() == {"Сервер команды": "test-model", "default-embed": "bge-m3", "Другая": "other"}
    assert (await client.get("/llm")).json()["roles"]["chat"]["provider"] == other


async def test_an_imported_model_is_not_imported_again(client, main_settings):
    providers.register_env_models(main_settings)
    default = (await client.get("/llm")).json()["roles"]["chat"]["provider"]

    # The user moves it to another address: the old LLM_* values must not bring it back.
    await client.patch(f"/llm/providers/{default}", json={"base_url": "http://moved:9000/v1"})
    providers.register_env_models(main_settings)

    assert _providers() == {"default": "test-model"}

    # Deleted (after moving the role away), it stays deleted.
    other = await _add(client, "Другая", "other")
    await client.patch("/llm", json={"chat": other})
    await client.delete(f"/llm/providers/{default}")
    providers.register_env_models(main_settings)

    assert _providers() == {"Другая": "other"}


async def test_a_new_model_in_the_variables_is_imported(client, main_settings, monkeypatch):
    providers.register_env_models(main_settings)

    monkeypatch.setenv("LLM_MODEL", "newer-model")
    providers.register_env_models(Settings.from_env("main"))

    assert _providers() == {"default": "test-model", "default 2": "newer-model"}


async def test_a_taken_default_name_gets_a_number(client, main_settings):
    await _add(client, "default", "something-else")

    providers.register_env_models(main_settings)

    assert _providers() == {"default": "something-else", "default 2": "test-model"}


async def test_a_chat_without_a_model_says_how_to_connect_one(main_settings):
    loop = AgentLoop(main_settings, LLM(main_settings), main=None, catalog=None)

    result = await loop.run_turn(None, "Привет")

    assert "POST /llm/providers" in result.content


class FakeClient:
    """A server whose `plain` model never calls tools."""

    async def chat(self, messages, *, model, tools=None, **_):
        if tools and model != "plain":
            return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "ping", "arguments": "{}"}}]}

        return {"content": "готов"}

    async def embed(self, texts, *, model):
        return [[0.1, 0.2, 0.3]]


@pytest.mark.parametrize(
    ("model", "role", "result"),
    [
        ("tools", "chat", {"ok": True}),
        ("plain", "chat", {"ok": False}),
        ("plain", "instruct", {"ok": True}),
        ("bge-m3", "embed", {"ok": True, "dimensions": 3}),
    ],
)
async def test_each_role_has_its_own_check(model, role, result):
    checked = await REAL_CHECK(FakeClient(), model, role)

    assert {key: checked[key] for key in result} == result
