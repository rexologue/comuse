"""Providers configured through main's API serve the roles on the next call; the
environment, when it names a model, decides instead."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from agent_loop.loop import AgentLoop
from common.errors import install_error_handlers
from common.llm import LLM, LLMNotConfigured
from common.settings import Settings
from main.api import llm as llm_api


@pytest.fixture
def db_settings(settings, monkeypatch) -> Settings:
    """No LLM_MODEL: the LLM comes from the database; keys go to an encrypted file."""
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.setenv("KEYRING_BACKEND", "file")
    monkeypatch.setenv("KEYRING_PASSWORD", "test-only")

    return Settings.from_env("main")


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(llm_api.router)
    app.state.settings = settings

    return app


@pytest.fixture
async def client(db_settings):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(_app(db_settings)), base_url="http://agent") as client:
        yield client


async def _add(client, name: str, **fields) -> str:
    response = await client.post("/llm/providers", json={"name": name, "base_url": "http://llm-host:8000/v1", "model_chat": "big", **fields})
    assert response.status_code == 200, response.text

    return response.json()["id"]


async def test_nothing_serves_the_roles_until_a_provider_is_added(client, db_settings):
    llm = LLM(db_settings)

    assert (await client.get("/llm")).json()["roles"] == {"chat": None, "instruct": None, "embed": None}

    with pytest.raises(LLMNotConfigured):
        await llm.chat.complete([{"role": "user", "content": "?"}])


async def test_the_first_provider_becomes_current_and_serves_every_role(client, db_settings):
    provider = await _add(client, "Сервер команды", model_embed="bge-m3")
    overview = (await client.get("/llm")).json()

    assert overview["provider"] == provider
    assert overview["roles"] == {
        "chat": {"provider": provider, "model": "big"},
        "instruct": {"provider": provider, "model": "big"},  # no instruct model: the chat one
        "embed": {"provider": provider, "model": "bge-m3"},
    }


async def test_switching_the_provider_applies_on_the_next_call(client, db_settings):
    llm = LLM(db_settings)
    first = await _add(client, "Первый")
    second = await _add(client, "Второй", model_chat="other", model_instruct="fast")

    assert llm.chat.endpoint().provider == first

    await client.patch("/llm", json={"provider": second})

    assert (llm.chat.endpoint().provider, llm.chat.endpoint().model) == (second, "other")
    assert llm.instruct.endpoint().model == "fast"


async def test_embeddings_may_come_from_another_provider(client, db_settings):
    chat = await _add(client, "Чат")
    embeddings = await _add(client, "Эмбеддинги", model_embed="bge-m3")

    assert LLM(db_settings).embed.endpoint() is None

    await client.patch("/llm", json={"embed_provider": embeddings})
    roles = (await client.get("/llm")).json()["roles"]

    assert roles["chat"]["provider"] == chat
    assert roles["embed"] == {"provider": embeddings, "model": "bge-m3"}


async def test_the_key_is_never_shown_and_a_new_one_reaches_the_services(client, db_settings):
    llm = LLM(db_settings)
    provider = await _add(client, "Облако", api_key="sk-first")

    assert "sk-first" not in (await client.get("/llm")).text
    assert llm.client(llm.chat.endpoint()).api_key == "sk-first"

    await client.patch(f"/llm/providers/{provider}", json={"api_key": "sk-second"})
    assert llm.client(llm.chat.endpoint()).api_key == "sk-second"

    removed = await client.patch(f"/llm/providers/{provider}", json={"api_key": None})
    assert removed.json()["has_key"] is False
    assert llm.client(llm.chat.endpoint()).api_key == ""


async def test_the_current_provider_stays_until_another_is_chosen(client):
    current = await _add(client, "Текущий")
    other = await _add(client, "Другой", model_embed="bge-m3")
    await client.patch("/llm", json={"embed_provider": other})

    assert (await client.delete(f"/llm/providers/{current}")).status_code == 409
    assert (await client.delete(f"/llm/providers/{other}")).status_code == 200
    assert (await client.get("/llm")).json()["embed_provider"] is None


async def test_a_provider_for_embeddings_only_cannot_be_current(client):
    embeddings = await _add(client, "Эмбеддинги", model_chat=None, model_embed="bge-m3")

    assert (await client.get("/llm")).json()["provider"] is None
    assert (await client.patch("/llm", json={"provider": embeddings})).status_code == 400
    assert (await client.patch("/llm", json={"embed_provider": embeddings})).status_code == 200


async def test_bad_provider_fields_are_refused(client):
    await _add(client, "Сервер")

    duplicate = await client.post("/llm/providers", json={"name": "Сервер", "base_url": "http://other/v1", "model_chat": "m"})
    no_scheme = await client.post("/llm/providers", json={"name": "Ещё", "base_url": "llm-host:8000/v1", "model_chat": "m"})

    assert duplicate.status_code == 409
    assert no_scheme.status_code == 422


async def test_the_environment_decides_and_locks_the_choice(settings):
    """LLM_MODEL is set in the test environment."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(_app(settings)), base_url="http://agent") as client:
        overview = (await client.get("/llm")).json()
        refused = await client.patch("/llm", json={"provider": "prv_any"})

    assert overview["locked"] is True
    assert overview["providers"][0]["id"] == "env"
    assert overview["roles"]["chat"] == {"provider": "env", "model": "test-model"}
    assert refused.status_code == 409


async def test_a_chat_without_a_model_says_how_to_connect_one(db_settings):
    loop = AgentLoop(db_settings, LLM(db_settings), main=None, catalog=None)

    result = await loop.run_turn(None, "Привет")

    assert "POST /llm/providers" in result.content


class FakeClient:
    """Answers like a provider whose `plain` model never calls tools."""

    async def models(self):
        return ["plain", "bge-m3"]

    async def chat(self, messages, *, model, tools=None, **_):
        if tools and model != "plain":
            return {"content": "", "tool_calls": [{"id": "1", "function": {"name": "ping", "arguments": "{}"}}]}

        return {"content": "готов"}

    async def embed(self, texts, *, model):
        return [[0.1, 0.2, 0.3]]


async def test_the_check_tells_which_role_fails_and_why():
    result = await llm_api._check(FakeClient(), {"chat": "plain", "instruct": "plain", "embed": "bge-m3"})

    assert result["ok"] is False
    assert result["list"] == {"ok": True, "models": ["bge-m3", "plain"]}
    assert result["roles"]["chat"]["ok"] is False and "вызов" in result["roles"]["chat"]["error"]
    assert result["roles"]["instruct"] == {"model": "plain", "ok": True}
    assert result["roles"]["embed"] == {"model": "bge-m3", "ok": True, "dimensions": 3}
