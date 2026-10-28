"""A tool's definition: what a task version is approved with, and its hash.

Built-in tool or own command, the rule is one: the version keeps the definition of
each tool its steps call (`TaskVersion.tools`); a tool whose hash is not the one kept
does not run in the task (the runner checks before a run, the executor on every call)
until the user looks at what changed and approves a version with the new definition.

The definition is what the call is: its schemas, settings, confirmation, description,
and an own command's code (`code_hash`, over its files). The card, the group's guide
and what the chat offers first are how a tool is shown, not what it does."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


FIELDS = (
    "name",
    "description",
    "confirm",
    "secrets",
    "timeout",
    "input_schema",
    "output_schema",
    "settings_schema",
    "code_hash",
)

# What the user reads about a change.
LABELS = {
    "description": "описание",
    "confirm": "подтверждение",
    "secrets": "аккаунты",
    "timeout": "таймаут",
    "input_schema": "аргументы",
    "output_schema": "результат",
    "settings_schema": "настройки",
    "code_hash": "код",
}


class Catalog(dict):
    """The tools by name as the executor lists them, and `unavailable`: the plugins whose
    server is not running (group → why). Their tools are missing for now, not gone: a
    task that calls them waits for the plugin, it is not a task with other tools."""

    def __init__(self, manifests: dict[str, dict[str, Any]] | None = None, unavailable: dict[str, str] | None = None):
        super().__init__(manifests or {})
        self.unavailable = dict(unavailable or {})


def down(tools: Iterable[str], catalog: dict[str, dict[str, Any]]) -> dict[str, str]:
    """{tool: why} for the tools of `tools` that are missing because their plugin is not running."""
    unavailable = getattr(catalog, "unavailable", {})

    return {
        name: unavailable[name.split(".", 1)[0]]
        for name in sorted(set(tools))
        if name not in catalog and name.split(".", 1)[0] in unavailable
    }


def definition(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: manifest[key] for key in FIELDS if manifest.get(key) is not None}


def definition_hash(manifest: dict[str, Any]) -> str:
    text = json.dumps(definition(manifest), sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot(tools: Iterable[str], catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{tool: its definition with `hash`} for the tools a version calls (those in the catalog)."""
    return {
        name: {**definition(catalog[name]), "hash": catalog[name]["hash"]}
        for name in sorted(set(tools))
        if name in catalog
    }


def changed(kept: dict[str, dict[str, Any]], tools: Iterable[str], catalog: dict[str, dict[str, Any]]) -> list[str]:
    """The tools of `tools` whose definition is not the kept one: changed, gone, or never
    kept (a version from before definitions were kept has `{}` for each). A tool whose
    plugin is not running is not among them (`down`)."""
    missing = down(tools, catalog)

    return sorted(
        name for name in set(tools) if name not in missing
        if name not in catalog or not kept.get(name, {}).get("hash") or kept[name]["hash"] != catalog[name].get("hash")
    )


def differences(kept: dict[str, dict[str, Any]], now: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """[{tool, fields}] between two snapshots: what changed in each tool both have."""
    found = []

    for name, new in sorted(now.items()):
        old = kept.get(name)
        if old is None:
            # A tool new to the version is a change of its steps, shown there.
            continue

        if not old.get("hash"):
            found.append({"tool": name, "fields": ["определение не было зафиксировано"]})

        elif old["hash"] != new.get("hash"):
            fields = [LABELS[key] for key in FIELDS if key in LABELS and old.get(key) != new.get(key)]
            found.append({"tool": name, "fields": fields or ["определение"]})

    return found


def changes(kept: dict[str, dict[str, Any]], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What changed in the kept tools since, those gone too: what the user reads before
    approving the task with the tools as they are now."""
    missing = down(kept, catalog)
    gone = [{"tool": name, "fields": ["команды больше нет"]} for name in sorted(kept) if name not in catalog and name not in missing]

    return differences(kept, snapshot(kept, catalog)) + gone
