"""tool.json: what the agent, the compiler and the executor know about a command."""

from __future__ import annotations

from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator

from common.tools.fields import PATH_ARG


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80, description="Короткое название для людей")
    description: str = Field(min_length=1, description="Что делает команда — модель выбирает её по этому тексту")
    # What the user sees before a call, with {argument} placeholders; the title if empty.
    card: str = ""
    # As internal tools: True — ask (the user may stop asking in a chat), "always", False.
    confirm: bool | Literal["always"] = True
    timeout: int | None = Field(default=None, ge=1, le=86400)
    # JSON Schema of `args`; a property marked {"x-path": "path"} (or "refs" for a list)
    # is a workspace path: checked before the run and handed over as a full path.
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            jsonschema.Draft202012Validator.check_schema(value)

        except jsonschema.SchemaError as e:
            raise ValueError(f"неверная JSON Schema: {e.message}") from e

        if value.get("type") != "object":
            raise ValueError('схема должна описывать объект: {"type": "object", ...}')

        return value


    def path_args(self) -> dict[str, str]:
        """{argument: "path" | "refs"} for the properties marked as workspace paths."""
        marks = {}

        for name, prop in (self.input_schema.get("properties") or {}).items():
            mark = prop.get(PATH_ARG) if isinstance(prop, dict) else None
            if mark is None and isinstance(prop, dict) and isinstance(prop.get("items"), dict):
                mark = prop["items"].get(PATH_ARG) and "refs"

            if mark in ("path", "refs"):
                marks[name] = mark

        return marks
