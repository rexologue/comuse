"""main answers only a client with the API token, only under an allowed Host, and lets
browsers of other origins in only when they are listed."""

from __future__ import annotations

import os
import stat

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI

from common.errors import install_error_handlers
from common.settings import Settings
from main.auth import Guard, api_token, require_client, token_path


def _app(settings: Settings) -> FastAPI:
    router = APIRouter(dependencies=[Depends(require_client)])
    router.add_api_route("/ping", lambda: {"ok": True})

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.add_middleware(Guard)
    app.state.settings = settings
    app.state.api_token = api_token(settings)

    return app


def _client(settings: Settings, base_url: str = "http://127.0.0.1:28077") -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(_app(settings)), base_url=base_url)


def _bearer(settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token(settings)}"}


async def test_every_request_needs_the_token(env, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "internal")
    settings = Settings.from_env("main")

    async with _client(settings) as client:
        assert (await client.get("/ping")).status_code == 401
        assert (await client.get("/ping", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await client.get("/ping", headers=_bearer(settings))).status_code == 200
        assert (await client.get("/ping", headers={"X-Internal-Token": "internal"})).status_code == 200


def test_without_api_token_main_makes_one_and_keeps_it(env):
    settings = Settings.from_env("main")

    token = api_token(settings)

    assert len(token) >= 32
    assert api_token(settings) == token
    if os.name != "nt":
        assert stat.S_IMODE(token_path(settings).stat().st_mode) == 0o600


def test_api_token_from_the_environment_wins(env, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "from-env")
    settings = Settings.from_env("main")

    assert api_token(settings) == "from-env"
    assert not token_path(settings).exists()


async def test_a_foreign_host_is_refused_before_the_token(env, monkeypatch):
    settings = Settings.from_env("main")

    async with _client(settings, "http://rebound.example:28077") as client:
        assert (await client.get("/ping", headers=_bearer(settings))).status_code == 400

    monkeypatch.setenv("ALLOWED_HOSTS", "*")
    settings = Settings.from_env("main")

    async with _client(settings, "http://rebound.example:28077") as client:
        assert (await client.get("/ping", headers=_bearer(settings))).status_code == 200


@pytest.mark.parametrize(("origins", "allowed"), [("", False), ("http://tauri.localhost", True)])
async def test_cors_only_for_listed_origins(env, monkeypatch, origins, allowed):
    monkeypatch.setenv("ALLOWED_ORIGINS", origins)
    settings = Settings.from_env("main")
    preflight = {"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}

    async with _client(settings) as client:
        listed = await client.options("/ping", headers=preflight)
        other = await client.options("/ping", headers={**preflight, "Origin": "http://evil.example"})

    assert (listed.headers.get("access-control-allow-origin") == "http://tauri.localhost") is allowed
    assert "access-control-allow-origin" not in other.headers
