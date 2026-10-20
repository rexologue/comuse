"""main's external API: task changes go through the orchestrator, there is no side door."""

from __future__ import annotations

from main.api import gateway


def test_no_catch_all_route_to_the_compiler():
    paths = {route.path for route in gateway.router.routes}

    assert "/compiler/compile" in paths and "/compiler/patch" in paths
    assert not any(path.startswith("/compiler/{") for path in paths)
