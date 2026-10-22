"""Logs go to a rotating file per process as well as the console; main shows their tail."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from common.service import configure_logging, log_file
from common.settings import Settings
from main.api import logs as logs_api


@pytest.fixture(autouse=True)
def no_leftover_handlers():
    yield

    for logger in (logging.getLogger(), logging.getLogger("uvicorn")):
        for handler in [h for h in logger.handlers if getattr(h, "agent_log", False)]:
            logger.removeHandler(handler)
            handler.close()


def _ours() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "agent_log", False)]


def test_one_file_for_one_process_and_a_file_per_service_otherwise(env, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "single")
    single = Settings.from_env("runner")
    monkeypatch.setenv("RUN_MODE", "multi")
    multi = Settings.from_env("runner")

    assert log_file(single) == env / "data" / "logs" / "agent.log"
    assert log_file(multi) == env / "data" / "logs" / "runner.log"


def test_every_service_of_one_process_shares_one_handler(env, monkeypatch):
    monkeypatch.setenv("RUN_MODE", "single")

    for name in ("main", "executor", "runner"):
        configure_logging(Settings.from_env(name))

    logging.getLogger("runner").warning("run failed")

    assert len(_ours()) == 1
    assert "run failed" in (env / "data" / "logs" / "agent.log").read_text(encoding="utf-8")


def test_log_dir_moves_the_files(env, monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "elsewhere"))

    configure_logging(Settings.from_env("scheduler"))
    logging.getLogger("scheduler").warning("cannot schedule")

    assert "cannot schedule" in (tmp_path / "elsewhere" / "scheduler.log").read_text(encoding="utf-8")


def test_the_tail_has_only_whole_lines(tmp_path):
    path = tmp_path / "agent.log"
    path.write_text("".join(f"line {n} " + "x" * 600 + "\n" for n in range(100)), encoding="utf-8")

    last = logs_api.tail(path, 3)

    assert [line.split()[1] for line in last] == ["97", "98", "99"]


async def test_main_shows_every_current_log(settings, env):
    logs = env / "data" / "logs"
    logs.mkdir(parents=True)
    (logs / "agent.log").write_text("a\nb\nc\n", encoding="utf-8")
    (logs / "agent.log.1").write_text("old\n", encoding="utf-8")
    (logs / "launcher.log").write_text("started\n", encoding="utf-8")

    app = FastAPI()
    app.include_router(logs_api.router)
    app.state.settings = settings
    app.state.api_token = "test-api-token"

    headers = {"Authorization": "Bearer test-api-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://agent", headers=headers) as client:
        response = await client.get("/logs", params={"lines": 2})

    assert response.json()["files"] == {"agent.log": ["b", "c"], "launcher.log": ["started"]}
