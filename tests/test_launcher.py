"""Ports are not fixed: main keeps one it found free, the others take any; one launcher
per data folder."""

from __future__ import annotations

import socket

import pytest

import launcher
from common.errors import ApiError
from main.registry import ServiceRegistry


def test_main_port_from_the_environment_wins(tmp_path):
    launcher._write_runtime(tmp_path, {"port": 23456})

    assert launcher._main_port(tmp_path, {"MAIN_PORT": "8000"}) == 8000


def test_main_keeps_last_times_port_while_it_is_free(tmp_path):
    first = launcher._main_port(tmp_path, {})
    launcher._write_runtime(tmp_path, {"port": first})

    assert launcher._MAIN_PORTS[0] <= first <= launcher._MAIN_PORTS[1]
    assert launcher._main_port(tmp_path, {}) == first


def test_a_taken_port_is_replaced(tmp_path):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        launcher._write_runtime(tmp_path, {"port": taken})

        assert launcher._main_port(tmp_path, {}) != taken


def test_every_service_gets_its_own_port_and_address():
    env = launcher._service_env("runner", {"PORT": "8000", "SELF_URL": "http://elsewhere"}, 41234)

    assert (env["SERVICE_NAME"], env["PORT"], env["SELF_URL"]) == ("runner", "41234", "http://127.0.0.1:41234")


def test_a_second_launcher_on_the_same_data_is_refused(tmp_path):
    first = launcher._InstanceLock(tmp_path / "launcher.lock")
    second = launcher._InstanceLock(tmp_path / "launcher.lock")

    try:
        assert first.acquire()
        assert not second.acquire()

        first.release()
        assert second.acquire()

    finally:
        second.release()


def test_main_does_not_guess_where_a_service_is():
    registry = ServiceRegistry(internal_token="")

    with pytest.raises(ApiError) as e:
        registry.url("runner")

    assert e.value.status_code == 503

    registry.register("runner", "http://127.0.0.1:41234/")
    assert registry.url("runner") == "http://127.0.0.1:41234"


def test_a_bad_main_port_stops_the_start(tmp_path):
    with pytest.raises(SystemExit, match="MAIN_PORT"):
        launcher._main_port(tmp_path, {"MAIN_PORT": "eighty"})
