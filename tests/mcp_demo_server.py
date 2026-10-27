"""A small MCP server over stdio for the aggregator's tests (tests/test_upstream.py):
started as a process of its own, like a plugin's server."""

from __future__ import annotations

import os
from typing import TypedDict

from mcp.server.fastmcp import FastMCP


server = FastMCP("demo")


class Sum(TypedDict):
    sum: int


@server.tool(description=os.environ.get("DEMO_ADD_DESCRIPTION", "Складывает два числа"))
def add(a: int, b: int) -> Sum:
    return {"sum": a + b}


@server.tool(name="say-hi", description="Здоровается", structured_output=False)
def say_hi(name: str) -> str:
    return f"Привет, {name}"


@server.tool(description="Имена переменных окружения процесса")
def env_names() -> list[str]:
    return sorted(os.environ)


@server.tool(description="Всегда ошибка")
def fail() -> str:
    raise ValueError("сломалось")


@server.tool(description="Процесс завершается посреди вызова")
def die() -> str:
    os._exit(1)


if __name__ == "__main__":
    server.run("stdio")
