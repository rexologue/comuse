"""RUN_MODE=single: the six services in one process, main at the root and the others
under /svc/<name>; each still keeps its own guard."""

from __future__ import annotations

import httpx

import launcher
from common.settings import Settings


def test_in_one_process_a_service_registers_its_prefix(env, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "single")
    monkeypatch.setenv("MAIN_URL", "http://127.0.0.1:28077")

    assert Settings.from_env("runner").service.self_url == "http://127.0.0.1:28077/svc/runner"
    assert Settings.from_env("main").service.self_url == "http://127.0.0.1:8000"


def test_the_launcher_starts_one_uvicorn_for_all(monkeypatch):
    env = launcher._service_env(launcher.SINGLE, {"SELF_URL": "http://elsewhere", "SERVICE_NAME": "runner"}, 28077)
    command = launcher._command(launcher.SINGLE, env, reload=False, port=28077)

    assert (env["RUN_MODE"], env["PORT"]) == ("single", "28077")
    assert "SELF_URL" not in env and "SERVICE_NAME" not in env
    assert command[command.index("uvicorn") + 1] == "single_app:app"
    assert command[command.index("--port") + 1] == "28077"


async def test_every_service_answers_on_one_port(env, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "single")
    monkeypatch.setenv("INTERNAL_TOKEN", "internal")
    monkeypatch.setenv("API_TOKEN", "api")

    import single_app

    async with single_app.app.router.lifespan_context(single_app.app):
        transport = httpx.ASGITransport(single_app.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:28077") as client:
            health = {name: (await client.get(f"/svc/{name}/health")).json()["service"] for name in single_app.SERVICES}
            main_health = (await client.get("/health")).json()["service"]
            no_token = await client.get("/svc/executor/tools")
            internal = await client.get("/svc/executor/tools", headers={"X-Internal-Token": "internal"})
            settings = await client.get("/settings", headers={"Authorization": "Bearer api"})

    assert health == {name: name for name in single_app.SERVICES}
    assert main_health == "main"
    assert no_token.status_code == 401
    assert internal.status_code == 200 and internal.json()["tools"]
    assert settings.status_code == 200
