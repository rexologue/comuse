"""Domain settings: what a tool domain (mail, fs…) lets the user tune per task.

A domain declares one flat pydantic model. A task that uses the domain's tools gets a
section with its defaults; the user changes values (never keys); the domain's tools read
their section when they run. Nothing outside the domain knows what the fields mean."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


_SCALARS = {"boolean", "integer", "number", "string"}


class DomainSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: ClassVar[str]


_DOMAINS: dict[str, type[DomainSettings]] = {}


def domain_settings(cls: type[DomainSettings]) -> type[DomainSettings]:
    """Register a domain's settings model; every field must be a plain value with a default."""
    for name, prop in cls.model_json_schema()["properties"].items():
        if prop.get("type") not in _SCALARS and "enum" not in prop:
            raise TypeError(f"{cls.domain} settings: `{name}` must be a plain value (bool, number, string, choice)")

    for name, field in cls.model_fields.items():
        if field.is_required():
            raise TypeError(f"{cls.domain} settings: `{name}` needs a default")

    if cls.domain in _DOMAINS and _DOMAINS[cls.domain] is not cls:
        raise ValueError(f"settings of domain {cls.domain} are registered twice")

    _DOMAINS[cls.domain] = cls

    return cls


def settings_model(domain: str) -> type[DomainSettings] | None:
    return _DOMAINS.get(domain)


def tool_domain(tool: str) -> str:
    """`mail.send` → `mail`."""
    return tool.split(".", 1)[0]


def settings_schema(tool: str) -> dict[str, Any] | None:
    model = settings_model(tool_domain(tool))
    return model.model_json_schema() if model else None
