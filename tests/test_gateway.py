"""main's external API: task changes go through the orchestrator, there is no side door."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from common.errors import install_error_handlers
from common.settings import Settings
from main.api import gateway, internal
from main.auth import api_token


def test_no_catch_all_route_to_the_compiler():
    paths = {route.path for route in gateway.router.routes}

    assert "/compiler/compile" in paths and "/compiler/patch" in paths
    assert not any(path.startswith("/compiler/{") for path in paths)


def _proxy_app(settings, seen: list[httpx.Request]) -> FastAPI:
    # MockTransport reads the body itself, and the proxy streams it: a bare transport.
    class Upstream(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, stream=httpx.ByteStream(b"{}"))

    class Registry:
        def url(self, name: str) -> str:
            return f"http://{name}.test/svc"

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(gateway.router)
    app.include_router(internal.router)
    app.state.settings = settings
    app.state.api_token = api_token(settings)
    app.state.registry = Registry()
    app.state.http = httpx.AsyncClient(transport=Upstream())

    return app


@pytest.mark.parametrize("path", ["/accounts/..%2Fexecute", "/external-tools/..%2F..%2F..%2Finternal%2Fregister", "/agent/a/%2E%2E/b"])
async def test_a_proxied_path_cannot_climb_out_of_its_route(env, monkeypatch, path):
    monkeypatch.setenv("INTERNAL_TOKEN", "internal")
    settings = Settings.from_env("main")
    seen: list[httpx.Request] = []

    async with httpx.AsyncClient(transport=httpx.ASGITransport(_proxy_app(settings, seen)), base_url="http://main.test") as client:
        response = await client.post(path, headers={"Authorization": f"Bearer {api_token(settings)}"})

    assert response.status_code == 400 and seen == []


async def test_the_clients_own_internal_token_stops_at_main(env, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "internal")
    settings = Settings.from_env("main")
    seen: list[httpx.Request] = []

    async with httpx.AsyncClient(transport=httpx.ASGITransport(_proxy_app(settings, seen)), base_url="http://main.test") as client:
        headers = {"Authorization": f"Bearer {api_token(settings)}", "X-Internal-Token": "forged"}
        assert (await client.get("/accounts/kinds", headers=headers)).status_code == 200

    assert seen[0].headers.get_list("x-internal-token") == ["internal"]


async def test_internal_routes_are_not_open_without_an_internal_token(env):
    settings = Settings.from_env("main")
    seen: list[httpx.Request] = []

    async with httpx.AsyncClient(transport=httpx.ASGITransport(_proxy_app(settings, seen)), base_url="http://main.test") as client:
        assert (await client.post("/executor/execute")).status_code == 401
        assert (await client.post("/executor/execute", headers=[(b"authorization", "Bearer é".encode())])).status_code == 401
        assert (await client.post("/executor/execute", headers={"Authorization": f"Bearer {api_token(settings)}"})).status_code == 200
