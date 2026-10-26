"""Settings.from_env — role fallbacks, coercion and bounds."""

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


def test_role_overrides(env, monkeypatch):
    monkeypatch.setenv("LLM_MODEL_CHAT", "big")
    monkeypatch.setenv("LLM_MODEL_INSTRUCT", "fast")
    s = Settings.from_env("main")

    assert (s.llm.model_chat, s.llm.model_instruct) == ("big", "fast")


def test_without_llm_model_the_environment_has_no_llm(env, monkeypatch):
    """Nothing to register as a provider then (main/providers.py)."""
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm-host:8000/v1")

    assert Settings.from_env("main").llm is None


@pytest.mark.parametrize(
    ("key", "value"),
    [("PORT", "70000"), ("KEYRING_BACKEND", "vault")],
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
    path.write_text('A=1 # comment\nB="two words"\n# skipped\nC=\nD="abc #def" # a password\nE=\'x\'\nF= # empty\nG=#hash\n', encoding="utf-8")

    # A quoted value is whole, " #" inside it included; `F= # empty` is an empty value.
    assert load_dotenv(path) == {"A": "1", "B": "two words", "C": "", "D": "abc #def", "E": "x", "F": "", "G": "#hash"}


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
