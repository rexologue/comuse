"""A task's domain settings: one section per tool domain its steps use.

The schemas come from the tool catalog (each manifest carries its domain's
`settings_schema`), so the compiler never imports tool code. The compiler and the
LLM never choose values: a section starts from the domain's defaults and only the
user changes it."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.tools.domain import tool_domain


def domain_schemas(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {tool_domain(name): m["settings_schema"] for name, m in catalog.items() if m.get("settings_schema")}


def sync(current: dict[str, Any], spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Sections for the domains the spec uses: the user's values are kept, a new domain
    starts from its defaults, a domain the spec no longer uses is dropped."""
    schemas = domain_schemas(catalog)
    used = {tool_domain(step.tool) for step in spec.all_steps() if step.tool}

    out: dict[str, Any] = {}
    for domain in sorted(used & set(schemas)):
        defaults = {key: prop.get("default") for key, prop in schemas[domain]["properties"].items()}
        kept = {key: value for key, value in (current.get(domain) or {}).items() if key in defaults}
        out[domain] = {**defaults, **kept}

    return out


def update(current: dict[str, Any], changes: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply the user's changes: values only, no new domains or keys."""
    schemas = domain_schemas(catalog)
    errors: list[str] = []
    out = {domain: dict(values) for domain, values in current.items()}

    for domain, values in changes.items():
        if domain not in current or domain not in schemas:
            errors.append(f"у задачи нет настроек «{domain}»; есть: {sorted(current)}")
            continue

        if not isinstance(values, dict):
            errors.append(f"{domain}: ожидается объект {{поле: значение}}")
            continue

        properties = schemas[domain]["properties"]
        for key, value in values.items():
            if key not in properties:
                errors.append(f"{domain}.{key}: такой настройки нет; есть: {sorted(properties)}")
                continue

            problems = [e.message for e in Draft202012Validator(properties[key]).iter_errors(value)]
            if problems:
                errors.append(f"{domain}.{key}: {'; '.join(problems)}")
                continue

            out[domain][key] = value

    if errors:
        raise ApiError(status_code=422, message="Настройки не изменены", details={"errors": errors})

    return out


def describe(current: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Values with their titles and descriptions, for showing to the user."""
    schemas = domain_schemas(catalog)

    return {
        domain: {
            key: {
                "value": value,
                "title": schemas.get(domain, {}).get("properties", {}).get(key, {}).get("title", key),
                "description": schemas.get(domain, {}).get("properties", {}).get(key, {}).get("description", ""),
                "schema": schemas.get(domain, {}).get("properties", {}).get(key, {}),
            }
            for key, value in values.items()
        }
        for domain, values in current.items()
    }
