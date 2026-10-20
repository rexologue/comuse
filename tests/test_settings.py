"""Settings.from_env — required vars, role fallbacks, coercion and bounds."""

from __future__ import annotations

import pytest

from common.settings import Settings, SettingsError, load_dotenv


def test_defaults(env):
    s = Settings.from_env("executor")

    assert s.service.port == 8004
    assert s.service.self_url == "http://127.0.0.1:8004"
    assert s.service.internal_token == ""
    assert s.storage.data_dir == env / "data"


def test_one_model_serves_every_role(env):
    s = Settings.from_env("main")

    assert s.llm.model_chat == "test-model"
    assert s.llm.model_instruct == "test-model"
    assert s.llm.model_embed == ""
    assert s.llm.chat_reasoning_effort == ""


def test_role_overrides(env, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CHAT", "big")
    monkeypatch.setenv("LLM_MODEL_INSTRUCT", "fast")
    s = Settings.from_env("main")

    assert (s.llm.model_chat, s.llm.model_instruct) == ("big", "fast")


def test_llm_model_is_required(env, monkeypatch):
    monkeypatch.delenv("LLM_MODEL")

    with pytest.raises(SettingsError):
        Settings.from_env("main")


@pytest.mark.parametrize(
    ("key", "value"),
    [("PORT", "70000"), ("KEYRING_BACKEND", "vault"), ("LLM_CHAT_REASONING_EFFORT", "max"), ("LLM_NO_PROXY", "maybe")],
)
def test_invalid_values(env, monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(SettingsError):
        Settings.from_env("main")


def test_unknown_service(env):
    with pytest.raises(SettingsError):
        Settings.from_env("billing")


def test_dotenv_comments_and_quotes(tmp_path):
    path = tmp_path / ".env"
    path.write_text('A=1 # comment\nB="two words"\n# skipped\nC=\n', encoding="utf-8")

    assert load_dotenv(path) == {"A": "1", "B": "two words", "C": ""}


def test_embeddings_default_to_the_chat_server(env, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://chat/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    s = Settings.from_env("agent_loop")

    assert (s.llm.embed_base_url, s.llm.embed_api_key) == ("https://chat/v1", "secret")


def test_separate_embeddings_server_does_not_get_the_chat_key(env, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://chat/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_EMBED_BASE_URL", "http://old-server/v1/")
    s = Settings.from_env("agent_loop")

    assert (s.llm.embed_base_url, s.llm.embed_api_key) == ("http://old-server/v1", "")


def test_an_empty_embeddings_key_means_unset(env, monkeypatch):
    """The .env templates ship LLM_EMBED_API_KEY= empty: the same server keeps its key."""
    monkeypatch.setenv("LLM_BASE_URL", "https://chat/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_EMBED_API_KEY", "")
    s = Settings.from_env("agent_loop")

    assert s.llm.embed_api_key == "secret"


def test_an_embeddings_key_of_its_own_wins(env, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_EMBED_API_KEY", "other")
    s = Settings.from_env("agent_loop")

    assert s.llm.embed_api_key == "other"
