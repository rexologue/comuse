"""plugin.json: what a plugin is and how it starts.

Either `tools` — functions `run(args, ctx)` in its files, which our shim (shim.py)
serves as an MCP server: the plugins we and the chat write; or `command` — the plugin's
own MCP server, a program of its .venv: a ready one from elsewhere. Everything else is
the same for both."""

from __future__ import annotations

import re
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from common.tools.fields import PATH_ARG


NAME_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"

Confirm = bool | Literal["always"]


def _object_schema(value: dict[str, Any]) -> dict[str, Any]:
    try:
        jsonschema.Draft202012Validator.check_schema(value)

    except jsonschema.SchemaError as e:
        raise ValueError(f"неверная JSON Schema: {e.message}") from e

    if value.get("type") != "object":
        raise ValueError('схема должна описывать объект: {"type": "object", ...}')

    return value


class ToolEntry(BaseModel):
    """One function of a plugin our shim serves."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=NAME_PATTERN, description="Имя инструмента в группе: plugin.<name>")
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, description="Что делает инструмент: модель выбирает его по этому тексту")
    # What the user sees before a call, with {argument} placeholders; the title if empty.
    card: str = ""
    # True — ask (the user may stop asking in a chat), "always", False — it only looks or computes.
    confirm: Confirm = True
    timeout: int | None = Field(default=None, ge=1, le=86400)
    # JSON Schema of `args`; a property marked {"x-path": "path"} (or "refs" for a list)
    # is a workspace path: checked before the call and handed over as a full path.
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    # The file with `def run(args, ctx)`.
    entry: str = "main.py"

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _object_schema(value)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80, description="Короткое название для людей")
    description: str = ""
    tools: list[ToolEntry] | None = None
    # The server's command: the first word is `python` (the plugin's own) or a program of
    # its .venv (a package's script); never anything from elsewhere on the machine.
    command: list[str] | None = None
    # Variables for the server's process, besides the few safe ones.
    env: dict[str, str] = Field(default_factory=dict)
    # Whether the chat asks before a call of a `command` server's tool: for all of them,
    # and per tool (its name on the server). A server's own hints are only its word.
    confirm: Confirm = True
    confirm_by_tool: dict[str, Confirm] = Field(default_factory=dict)
    # The plugin does as a dry run asks (_meta context.dry_run): our functions do (ctx.dry_run),
    # a server from elsewhere does not unless it says so; then in a dry run only its tools
    # that never ask are called.
    dry_run: bool | None = None
    timeout: int | None = Field(default=None, ge=1, le=86400)

    @model_validator(mode="after")
    def _one_way_to_start(self) -> PluginManifest:
        if (self.tools is None) == (self.command is None):
            raise ValueError("нужно одно из двух: tools (функции run) или command (свой MCP-сервер)")

        if self.tools is not None:
            names = [tool.name for tool in self.tools]
            if not names:
                raise ValueError("tools: хотя бы один инструмент")
            if len(set(names)) != len(names):
                raise ValueError("tools: имена инструментов повторяются")

        if self.command is not None:
            if not self.command or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.command[0]):
                raise ValueError("command: первое слово — python или программа из .venv плагина, без пути")

        return self


    def handles_dry_run(self) -> bool:
        return self.dry_run if self.dry_run is not None else self.tools is not None


    def entry_of(self, name: str) -> ToolEntry | None:
        return next((tool for tool in self.tools or [] if tool.name == name), None)


def path_args(schema: dict[str, Any]) -> dict[str, str]:
    """{argument: "path" | "refs"} for the properties marked as workspace paths."""
    marks = {}

    for name, prop in (schema.get("properties") or {}).items():
        mark = prop.get(PATH_ARG) if isinstance(prop, dict) else None
        if mark is None and isinstance(prop, dict) and isinstance(prop.get("items"), dict):
            mark = prop["items"].get(PATH_ARG) and "refs"

        if mark in ("path", "refs"):
            marks[name] = mark

    return marks
