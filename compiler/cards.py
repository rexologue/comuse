"""Human-readable view of a task: step cards, the trigger in words, version diffs."""

from __future__ import annotations

import re
import string
from typing import Any

from common.cron import describe_schedule
from common.schemas.task import Step, TaskSpec, Trigger


_EXPR = re.compile(r"\{\{\s*(.*?)\s*\}\}")


def build_cards(spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [_card(step, catalog) for step in spec.steps]


def describe_trigger(trigger: Trigger) -> str:
    return describe_schedule(trigger.type, trigger.expr)


def summarize(spec: TaskSpec) -> str:
    count = len(spec.steps)
    return f"{spec.title}: {describe_trigger(spec.trigger)}, шагов: {count}"


def diff_specs(old: TaskSpec, new: TaskSpec, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What changes for the user between two versions, as before/after lines."""
    old_cards = {c["id"]: c for c in _flatten(build_cards(old, catalog))}
    new_cards = {c["id"]: c for c in _flatten(build_cards(new, catalog))}

    changes: list[dict[str, Any]] = []

    for step_id, card in new_cards.items():
        before = old_cards.get(step_id)
        if before is None:
            changes.append({"kind": "added", "step": step_id, "after": card["text"]})

        elif before["text"] != card["text"] or before["args"] != card["args"]:
            changes.append({"kind": "changed", "step": step_id, "before": before["text"], "after": card["text"], "args_before": before["args"], "args_after": card["args"]})

    for step_id, card in old_cards.items():
        if step_id not in new_cards:
            changes.append({"kind": "removed", "step": step_id, "before": card["text"]})

    if old.trigger != new.trigger:
        changes.append({"kind": "trigger", "before": describe_trigger(old.trigger), "after": describe_trigger(new.trigger)})

    if old.title != new.title:
        changes.append({"kind": "title", "before": old.title, "after": new.title})


    return {"changes": changes, "lines": [_line(c) for c in changes]}


def _card(step: Step, catalog: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if step.foreach is not None:
        return {
            "id": step.id,
            "tool": None,
            "text": step.title or f"Для каждого элемента из {_readable(step.foreach)}:",
            "args": {},
            "children": [_card(child, catalog) for child in step.steps or []],
        }

    manifest = catalog.get(step.tool or "", {})
    template = manifest.get("card") or step.tool or step.id

    return {
        "id": step.id,
        "tool": step.tool,
        "confirm": manifest.get("confirm"),
        "text": step.title or _format_card(template, step.args),
        "args": step.args,
        "children": [],
    }


def _format_card(template: str, args: dict[str, Any]) -> str:
    values = {key: _readable(value) for key, value in args.items()}
    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}

    return template.format(**{name: values.get(name, "—") for name in fields})


def _readable(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "—"

    if isinstance(value, list):
        return ", ".join(_readable(v) for v in value)

    if isinstance(value, dict):
        # Field mappings read best as their names: «client, amount».
        return ", ".join(str(key) for key in value) if value else "—"

    # `{{ week_start }}` reads as ‹week_start› on a card.
    return _EXPR.sub(lambda m: f"‹{m.group(1)}›", str(value))


def _flatten(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat = []
    for card in cards:
        flat.append(card)
        flat += _flatten(card["children"])

    return flat


def _line(change: dict[str, Any]) -> str:
    kind = change["kind"]

    if kind == "added":
        return f"+ новый шаг: {change['after']}"

    if kind == "removed":
        return f"− убран шаг: {change['before']}"

    if kind == "changed":
        return f"~ было: {change['before']} → стало: {change['after']}"

    if kind == "trigger":
        return f"~ расписание: {change['before']} → {change['after']}"

    return f"~ название: {change['before']} → {change['after']}"
