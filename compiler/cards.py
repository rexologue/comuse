"""Human-readable view of a task: step cards, the trigger in words, version diffs."""

from __future__ import annotations

import re
import string
from typing import Any

from common.schemas.task import Permissions, Step, TaskSpec, Trigger


_EXPR = re.compile(r"\{\{\s*(.*?)\s*\}\}")

_DAYS = {
    "MON": "понедельникам", "TUE": "вторникам", "WED": "средам", "THU": "четвергам",
    "FRI": "пятницам", "SAT": "субботам", "SUN": "воскресеньям",
    "1": "понедельникам", "2": "вторникам", "3": "средам", "4": "четвергам",
    "5": "пятницам", "6": "субботам", "0": "воскресеньям", "7": "воскресеньям",
}


def build_cards(spec: TaskSpec, catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [_card(step, catalog) for step in spec.steps]


def describe_trigger(trigger: Trigger) -> str:
    if trigger.type == "cron":
        return _describe_cron(trigger.expr or "")

    if trigger.type == "file_appeared":
        return f"когда в папке «{trigger.folder or 'рабочая папка'}» появляется файл «{trigger.pattern}»"

    if trigger.type == "new_email":
        filters = ", ".join(f"{k}: {v}" for k, v in trigger.mail_filter.items())
        return f"при новом письме в «{trigger.account}»" + (f" ({filters})" if filters else "")

    return "только вручную"


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

    changes += _permission_changes(old.permissions, new.permissions)

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
        "side_effect": manifest.get("side_effect"),
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
        return "{…}"

    # `{{ week_start }}` reads as ‹week_start› on a card.
    return _EXPR.sub(lambda m: f"‹{m.group(1)}›", str(value))


def _describe_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return f"по расписанию «{expr}»"

    minute, hour, dom, month, dow = parts

    if not (minute.isdigit() and hour.isdigit()):
        return f"по расписанию «{expr}»"

    at = f"в {int(hour):02d}:{int(minute):02d}"

    if dom == "*" and month == "*" and dow == "*":
        return f"каждый день {at}"

    if dom == "*" and month == "*":
        days = [_DAYS.get(d.upper(), d) for d in dow.split(",")]
        if dow.upper() in ("MON-FRI", "1-5"):
            return f"по будням {at}"

        return f"по {', '.join(days)} {at}"

    if dom.isdigit() and month == "*" and dow == "*":
        return f"каждый месяц {int(dom)}-го числа {at}"

    return f"по расписанию «{expr}»"


def _flatten(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flat = []
    for card in cards:
        flat.append(card)
        flat += _flatten(card["children"])

    return flat


def _permission_changes(old: Permissions, new: Permissions) -> list[dict[str, Any]]:
    labels = {
        "read_folders": "чтение папок",
        "write_folders": "запись в папки",
        "mail_accounts": "почтовые аккаунты",
        "mail_send_to": "адресаты писем",
        "net_domains": "сайты",
    }

    changes = []
    for field, label in labels.items():
        before, after = set(getattr(old, field)), set(getattr(new, field))

        if after - before:
            changes.append({"kind": "permission_added", "what": label, "items": sorted(after - before)})

        if before - after:
            changes.append({"kind": "permission_removed", "what": label, "items": sorted(before - after)})

    return changes


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

    if kind == "title":
        return f"~ название: {change['before']} → {change['after']}"

    if kind == "permission_added":
        return f"+ разрешено ({change['what']}): {', '.join(change['items'])}"

    return f"− больше не разрешено ({change['what']}): {', '.join(change['items'])}"
