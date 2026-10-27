"""Human-readable view of a task: step cards, the trigger in words, version diffs."""

from __future__ import annotations

import re
import string
from typing import Any

from common import templating
from common.cron import describe_schedule
from common.schemas.task import Step, TaskSpec, Trigger


_EXPR = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}", re.S)
_STATEMENT = re.compile(r"\{%-?\s*(.*?)\s*-?%\}", re.S)

# What a step does besides its card text and arguments: a change here is a change too.
_BEHAVIOUR = {
    "tool": "команда",
    "when": "условие",
    "foreach": "перебор",
    "as_": "имя элемента",
    "on_error": "при ошибке",
    "on_empty": "если пусто",
    "retries": "повторов",
}



def build_cards(spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [_card(step, catalog) for step in spec.steps]


def describe_trigger(trigger: Trigger) -> str:
    return describe_schedule(trigger.type, trigger.expr)


def summarize(spec: TaskSpec) -> str:
    count = len(spec.steps)
    return f"{spec.title}: {describe_trigger(spec.trigger)}, шагов: {count}"


def diff_specs(
    old: TaskSpec,
    new: TaskSpec,
    catalog: dict[str, dict[str, Any]],
    tools: list[dict[str, Any]] = (),
) -> dict[str, Any]:
    """What changes for the user between two versions, as before/after lines; `tools`:
    the tools whose definition changed between them (common/tools/definition.py)."""
    old_cards = {c["id"]: c for c in _flatten(build_cards(old, catalog))}
    new_cards = {c["id"]: c for c in _flatten(build_cards(new, catalog))}
    old_steps = {step.id: step for step in old.all_steps()}
    new_steps = {step.id: step for step in new.all_steps()}

    changes: list[dict[str, Any]] = []

    for step_id, card in new_cards.items():
        before = old_cards.get(step_id)
        if before is None:
            changes.append({"kind": "added", "step": step_id, "after": card["text"]})
            continue

        behaviour = _behaviour(old_steps[step_id], new_steps[step_id])
        if before["text"] != card["text"] or before["args"] != card["args"] or behaviour:
            changes.append({
                "kind": "changed", "step": step_id, "before": before["text"], "after": card["text"],
                "args_before": before["args"], "args_after": card["args"],
                "behaviour": behaviour,
            })

    for step_id, card in old_cards.items():
        if step_id not in new_cards:
            changes.append({"kind": "removed", "step": step_id, "before": card["text"]})

    kept_old = [step_id for step_id in old_steps if step_id in new_steps]
    kept_new = [step_id for step_id in new_steps if step_id in old_steps]
    if kept_old != kept_new:
        changes.append({"kind": "order", "before": ", ".join(kept_old), "after": ", ".join(kept_new)})

    if old.variables != new.variables:
        changes.append({"kind": "variables", "before": _pairs(old.variables), "after": _pairs(new.variables)})

    if old.description != new.description:
        changes.append({"kind": "description", "before": old.description or "—", "after": new.description or "—"})

    if old.trigger != new.trigger:
        changes.append({"kind": "trigger", "before": describe_trigger(old.trigger), "after": describe_trigger(new.trigger)})

    if old.title != new.title:
        changes.append({"kind": "title", "before": old.title, "after": new.title})

    for item in tools:
        changes.append({"kind": "tool", "tool": item["tool"], "fields": item["fields"]})

    return {"changes": changes, "lines": [_line(c) for c in changes]}


def _behaviour(old: Step, new: Step) -> list[dict[str, Any]]:
    return [
        {"field": label, "before": getattr(old, name), "after": getattr(new, name)}
        for name, label in _BEHAVIOUR.items()
        if getattr(old, name) != getattr(new, name)
    ]


def _pairs(values: dict[str, str]) -> str:
    return ", ".join(f"{key} = {_readable(value)}" for key, value in values.items()) or "—"


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
        "text": step.title or _format_card(template, step.args, fallback=manifest.get("title") or step.tool or step.id),
        "args": step.args,
        "children": [],
    }


def _format_card(template: str, args: dict[str, Any], *, fallback: str) -> str:
    """The card with the step's arguments; a card a person or the chat wrote may be
    broken (`{0}`, `{amount:.2f}`, a lone `{`), and then it is just the tool's title."""
    values = {key: _readable(value) for key, value in args.items()}

    try:
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        return template.format(**{name: values.get(name, "—") for name in fields})

    except (ValueError, IndexError, KeyError, AttributeError):
        return fallback


def _readable(value: Any) -> str:
    if value is None or value == "" or value == []:
        return "—"

    if isinstance(value, list):
        return ", ".join(_readable(v) for v in value)

    if isinstance(value, dict):
        # Field mappings read best as their names: «client, amount».
        return ", ".join(str(key) for key in value) if value else "—"

    return _template_text(str(value))


def _template_text(text: str) -> str:
    """A template as a card reads it: `{{ week_start }}` → ‹week_start›, `{% for f in
    files %}…{% endfor %}` → «для каждого из ‹files›: …», printing filters dropped."""

    def statement(match: re.Match) -> str:
        words = match.group(1).split()

        if words[:1] == ["for"] and "in" in words:
            return f"для каждого из ‹{_value(' '.join(words[words.index('in') + 1:]))}›: "

        if words[:1] in (["if"], ["elif"]):
            return f"если ‹{_value(' '.join(words[1:]))}›: "

        if words[:1] == ["else"]:
            return " иначе: "

        return " "

    def expression(match: re.Match) -> str:
        value = _value(match.group(1))
        return "№" if value == "loop.index" else f"‹{value}›"

    text = _EXPR.sub(expression, _STATEMENT.sub(statement, text))

    return " ".join(text.split())


def _value(expression: str) -> str:
    """The expression without its printing filters; `| length` reads as «число …»."""
    head, *filters = [part.strip() for part in expression.split("|")]
    kept = [f for f in filters if f.split("(")[0].strip() not in templating.PRINTING | {"length"}]
    text = " | ".join([head, *kept])

    return f"число {text}" if any(f.split("(")[0].strip() == "length" for f in filters) else text


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
        notes = [
            f"{item['field']}: {_readable(item['before'])} → {_readable(item['after'])}" for item in change.get("behaviour") or []
        ]
        if change["before"] == change["after"] and change.get("args_before") == change.get("args_after"):
            return f"~ {change['after']}: {'; '.join(notes)}"

        line = f"~ было: {change['before']} → стало: {change['after']}"
        return f"{line} ({'; '.join(notes)})" if notes else line

    if kind == "tool":
        return f"~ изменилась команда `{change['tool']}`: {', '.join(change['fields'])}"

    labels = {"trigger": "расписание", "order": "порядок шагов", "variables": "переменные", "description": "описание"}

    return f"~ {labels.get(kind, 'название')}: {change['before']} → {change['after']}"
