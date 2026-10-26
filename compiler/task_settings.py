"""A task's settings (common/tools/settings.py): what the user changed in the settings
of the tools its steps run, for the whole task or one step.

The schemas come from the tool catalog (each manifest carries its tool's
`settings_schema`), so the compiler never imports tool code. The compiler and the LLM
never choose values: everything starts at the tool's default and only the user changes it."""

from __future__ import annotations

import copy
from typing import Any

from jsonschema import Draft202012Validator

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.tools.settings import changed, tool_group


def sync(current: dict[str, Any], spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What the user changed, for a new version of the task: kept for the tools it still
    runs, and for each step that is still there with the same tool."""
    steps = _steps(spec)
    out: dict[str, Any] = {}

    for tool, properties in _schemas(spec, catalog).items():
        kept: dict[str, Any] = {}

        for field, levels in changed(current, tool).items():
            if field not in properties:
                continue

            level = {"all": levels["all"]} if "all" in levels else {}
            own = {step: value for step, value in (levels.get("steps") or {}).items() if step in steps[tool]}
            if own:
                level["steps"] = own
            if level:
                kept[field] = level

        if kept:
            out.setdefault(tool_group(tool), {})[tool] = kept

    return out


def apply(current: dict[str, Any], changes: list[dict[str, Any]], spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The user's changes, each `{tool, field, value, step?}`: no `step` — for the whole
    task, `step` — for that step; `value: null` takes the change back (the step goes back
    to the task's value, the task's to the default). All or nothing."""
    schemas, steps = _schemas(spec, catalog), _steps(spec)
    out = copy.deepcopy(current or {})
    errors: list[str] = []

    for change in changes:
        tool, field, step, value = change.get("tool"), change.get("field"), change.get("step"), change.get("value")
        where = f"{tool}.{field}" + (f" (шаг {step})" if step else "")

        if tool not in schemas:
            errors.append(f"{where}: у команд задачи нет настроек `{tool}`; есть: {sorted(schemas)}")
            continue

        if field not in schemas[tool]:
            errors.append(f"{where}: такой настройки нет; есть: {sorted(schemas[tool])}")
            continue

        if step is not None and step not in steps[tool]:
            errors.append(f"{where}: шага `{step}` с этой командой в задаче нет; есть: {steps[tool]}")
            continue

        if value is not None:
            problems = [e.message for e in Draft202012Validator(schemas[tool][field]).iter_errors(value)]
            if problems:
                errors.append(f"{where}: {'; '.join(problems)}")
                continue

        levels = out.setdefault(tool_group(tool), {}).setdefault(tool, {}).setdefault(field, {})
        if step is None:
            _set(levels, "all", value)
        else:
            _set(levels.setdefault("steps", {}), step, value)

    if errors:
        raise ApiError(status_code=422, message="Настройки не изменены", details={"errors": errors})

    return sync(out, spec, catalog)


def view(current: dict[str, Any], spec: TaskSpec, catalog: dict[str, dict[str, Any]], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For the task's panel: per tool its fields, each with the default, the task's value
    and every step that runs the tool, with the step's own value and what it runs on."""
    steps, texts = _steps(spec), _card_texts(cards)
    out = []

    for tool, properties in _schemas(spec, catalog).items():
        levels_of = changed(current, tool)
        fields = []

        for name, prop in properties.items():
            levels = levels_of.get(name) or {}
            default, task_value = prop.get("default"), levels.get("all")
            task_effective = default if task_value is None else task_value

            fields.append({
                "name": name,
                "title": prop.get("title") or name,
                "description": prop.get("description") or "",
                "schema": prop,
                "default": default,
                "all": task_value,
                "steps": [
                    {
                        "step": step,
                        "card": texts.get(step, step),
                        "value": (levels.get("steps") or {}).get(step),
                        "effective": (levels.get("steps") or {}).get(step, task_effective),
                    }
                    for step in steps[tool]
                ],
            })

        out.append({"group": tool_group(tool), "tool": tool, "tool_title": catalog[tool].get("title") or tool, "fields": fields})

    return out


def lines(settings_view: list[dict[str, Any]]) -> list[str]:
    """The task-wide values in words, for the agent to tell the user: «Отправлять письма
    сразу: выключено (по умолчанию). …» — a bare false next to the title reads as the
    opposite to a model."""
    out = []

    for tool in settings_view:
        for field in tool["fields"]:
            value = field["default"] if field["all"] is None else field["all"]
            state = ("включено" if value else "выключено") if isinstance(value, bool) else str(value)
            origin = " (по умолчанию)" if field["all"] is None else ""
            out.append(f"{field['title']}: {state}{origin}. {field['description']}".strip())

    return out


def _schemas(spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{tool: {field: schema}} for the tools the spec runs that have settings, in step order."""
    out: dict[str, dict[str, Any]] = {}

    for step in spec.all_steps():
        schema = (catalog.get(step.tool or "") or {}).get("settings_schema")
        if schema and step.tool not in out:
            out[step.tool] = schema.get("properties") or {}

    return out


def _steps(spec: TaskSpec) -> dict[str, list[str]]:
    """{tool: [ids of the steps that run it]}, in step order."""
    out: dict[str, list[str]] = {}

    for step in spec.all_steps():
        if step.tool:
            out.setdefault(step.tool, []).append(step.id)

    return out


def _card_texts(cards: list[dict[str, Any]]) -> dict[str, str]:
    texts: dict[str, str] = {}

    for card in cards or []:
        texts[card["id"]] = card["text"]
        texts.update(_card_texts(card.get("children") or []))

    return texts


def _set(level: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        level.pop(key, None)
    else:
        level[key] = value
