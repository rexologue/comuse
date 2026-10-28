"""A small MCP server over stdio for the aggregator's tests (tests/test_upstream.py):
started as a process of its own, like a plugin's server."""

from __future__ import annotations

import os
from pathlib import Path
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


@server.tool(description="Читает файлы по полным путям")
def read_files(paths: list[str]) -> list[str]:
    return [Path(path).read_text(encoding="utf-8") for path in paths]


@server.tool(description="Пишет файл по полному пути")
def write_file(path: str, text: str) -> str:
    Path(path).write_text(text, encoding="utf-8")
    return path


@server.tool(description="Сырая команда протокола")
def raw_command(command: str) -> str:
    return command


@server.tool(description="Всегда ошибка")
def fail() -> str:
    raise ValueError("сломалось")


@server.tool(description="Процесс завершается посреди вызова")
def die() -> str:
    os._exit(1)


if __name__ == "__main__":
    server.run("stdio")
