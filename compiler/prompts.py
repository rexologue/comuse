from __future__ import annotations

import json
from datetime import date
from typing import Any

from common.tools.guides import collect


# Deliberately a different domain from the demo scenarios: models copy example steps
# verbatim (a mail step the user never asked for), so the example shows the mechanics only.
_EXAMPLE = {
    "schema_version": 1,
    "title": "Сводка по командировкам за месяц",
    "description": "Посчитать расходы на командировки за прошлый месяц по сотрудникам и сделать сводку",
    "trigger": {"type": "cron", "expr": "0 9 1 * *"},
    "variables": {
        "month_start": "{{ today | start_of_prev_month }}",
        "month_end": "{{ today | end_of_prev_month }}",
    },
    "steps": [
        {
            "id": "reports",
            "tool": "fs.list",
            "args": {
                "folder": "Бухгалтерия/Командировки",
                "pattern": "*.xlsx",
                "modified_from": "{{ month_start }}",
                "modified_to": "{{ month_end }}",
            },
            "on_empty": "skip",
        },
        {
            "id": "totals",
            "tool": "xlsx.aggregate",
            "args": {
                "inputs": "{{ reports.output.files }}",
                "sum_columns": ["Сумма"],
                "group_by": "Сотрудник",
            },
        },
        {
            "id": "summary",
            "tool": "docs.fill_template",
            "args": {
                "template": "data:templates/travel_summary.docx",
                "context": {
                    "month": "{{ month_start | format_date('%m.%Y') }}",
                    "total": "{{ totals.output.totals['Сумма'] }}",
                    "rows": "{{ totals.output.groups }}",
                },
                "output_name": "Командировки {{ month_start | format_date('%m.%Y') }}.docx",
            },
        },
        {
            "id": "done",
            "tool": "user.notify",
            "args": {"message": "Сводка по командировкам готова: {{ summary.output.path }}"},
        },
    ],
}

_FOREACH_EXAMPLE = {
    "id": "sort",
    "foreach": "{{ files.output.files }}",
    "as": "file",
    "steps": [
        {"id": "kind", "tool": "llm.classify", "args": {"input": "{{ file.path }}", "labels": ["договор", "счёт", "акт"]}},
        {"id": "move", "tool": "fs.move", "args": {"src": "{{ file.path }}", "dst": "Документы/{{ kind.output.label }}/"}},
    ],
}


RULES = f"""Ты превращаешь разговор пользователя с ассистентом в повторяемую задачу — JSON по схеме TaskSpec.
Задача будет выполняться без LLM-планирования: шаги идут строго по порядку, аргументы подставляются из шаблонов.

Правила:
1. Используй только инструменты из каталога и только их аргументы (имена и типы — по input-схемам).
2. Повтори успешную последовательность действий из разговора. Неудачные попытки, разведку и вопросы — выброси.
   Не добавляй действий, которых не было в разговоре и о которых пользователь не просил (например, отправку
   писем): пример ниже показывает только формат, а не состав шагов.
3. Обобщай конкретику: даты — через variables и фильтры, конкретные значения из разговора — шагами, которые
   найдут их при запуске (как именно — в инструкциях групп ниже).
4. Ссылка на результат шага: {{{{ <id_шага>.output.<поле> }}}} — поля бери из output-схемы инструмента.
   Строка, которая целиком является одним {{{{ … }}}}, подставляется как значение (список остаётся списком).
5. Доступно в шаблонах: today, now, variables, результаты шагов выше, переменная foreach (as). Фильтры дат:
   start_of_week, end_of_week, start_of_prev_week, end_of_prev_week, start_of_month, end_of_month,
   start_of_prev_month, end_of_prev_month, add_days(n), format_date('%d.%m.%Y'). Для чисел в тексте —
   format_number, для дат — format_date (по умолчанию ДД.ММ.ГГГГ).
6. Для обработки каждого файла по отдельности — foreach с вложенными steps, например:
   {json.dumps(_FOREACH_EXAMPLE, ensure_ascii=False)}
7. Пути — только относительные (как в результатах инструментов) или data:…; никаких абсолютных путей.
8. trigger: cron из 5 полей (дни недели только именами MON..SUN, не числами) или manual — по просьбе пользователя. Trigger задаёт
   только время запуска; что искать (письма, файлы), делают шаги задачи.
9. Ограничения на значения, которые станут известны только при запуске, задаются аргументами самих
   инструментов (см. описания аргументов в каталоге).
10. id шагов — латиницей snake_case, короткие и понятные.
11. Задача никого ни о чём не спрашивает и не ждёт: вопросы и подтверждения из разговора в неё не переносятся.
12. Тексты, которые пользователь написал сам (текст письма, тему, формулировки), переноси дословно, символ
    в символ: копируй из разговора, не пересказывай и не набирай заново.

Пример задачи:
{json.dumps(_EXAMPLE, ensure_ascii=False, indent=1)}"""


def compile_messages(
    *,
    transcript: str,
    catalog: dict[str, dict[str, Any]],
    spec_schema: dict[str, Any],
    title: str | None,
    hint: str | None,
    today: date,
) -> list[dict[str, Any]]:
    user = (
        f"Сегодня {today.isoformat()}.\n\n"
        f"Каталог инструментов:\n{format_catalog(catalog)}\n\n"
        f"JSON Schema TaskSpec:\n{json.dumps(spec_schema, ensure_ascii=False)}\n\n"
        f"Разговор и вызовы инструментов:\n{transcript}\n\n"
        f"Пожелания пользователя к задаче: {hint or 'нет'}\n"
        f"{('Название задачи: ' + title) if title else ''}\n\n"
        "Верни только JSON задачи."
    )

    return [{"role": "system", "content": rules(catalog)}, {"role": "user", "content": user}]


def rules(catalog: dict[str, dict[str, Any]]) -> str:
    """The compiler's own rules plus every tool group's instructions for tasks."""
    guides = collect(list(catalog.values()), "compiler")
    if not guides:
        return RULES

    return RULES + "\n\nИнструкции групп инструментов:\n" + "\n".join(f"- {g}" for g in guides)


def patch_messages(
    *,
    spec: dict[str, Any],
    instruction: str,
    catalog: dict[str, dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    system = (
        rules(catalog)
        + "\n\nСейчас нужно ИЗМЕНИТЬ существующую задачу. Верни JSON вида "
        '{"summary": "что изменится, одной фразой", "ops": [JSON Patch RFC 6902]}. '
        "Пути в ops — JSON Pointer по текущей задаче, например /steps/3/args/cc. "
        "Меняй только то, о чём просит пользователь."
    )
    user = (
        f"Сегодня {today.isoformat()}.\n\n"
        f"Каталог инструментов:\n{format_catalog(catalog)}\n\n"
        f"Текущая задача:\n{json.dumps(spec, ensure_ascii=False, indent=1)}\n\n"
        f"Что изменить: {instruction}"
    )

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


PATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "ops": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "remove", "replace", "move", "copy", "test"]},
                    "path": {"type": "string"},
                    "value": {},
                    "from": {"type": "string"},
                },
                "required": ["op", "path"],
            },
        },
    },
    "required": ["summary", "ops"],
}


def format_catalog(catalog: dict[str, dict[str, Any]]) -> str:
    lines = []

    for name, manifest in sorted(catalog.items()):
        args = _describe_fields(manifest["input_schema"], with_required=True)
        output = _describe_fields(manifest["output_schema"], with_required=False)
        lines.append(
            f"- {name}: {manifest['description']}\n"
            f"    аргументы: {args}\n"
            f"    результат (.output): {output}"
        )

    return "\n".join(lines)


def _describe_fields(schema: dict[str, Any], *, with_required: bool) -> str:
    required = set(schema.get("required", []))
    parts = []

    for key, prop in schema.get("properties", {}).items():
        resolved = _resolve(prop, schema.get("$defs", {}))
        text = f"{key}: {_type_of(resolved, schema.get('$defs', {}))}"

        if with_required and key in required:
            text += " (обяз.)"

        if resolved.get("description"):
            text += f" — {resolved['description']}"

        parts.append(text)

    return "; ".join(parts) or "—"


def _resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref")
    if ref and ref.startswith("#/$defs/"):
        return {**defs.get(ref.split("/")[-1], {}), **{k: v for k, v in prop.items() if k != "$ref"}}

    return prop


def _type_of(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    if "anyOf" in prop:
        return " | ".join(_type_of(_resolve(p, defs), defs) for p in prop["anyOf"])

    kind = prop.get("type", "any")

    if kind == "array":
        return f"list[{_type_of(_resolve(prop.get('items', {}), defs), defs)}]"

    if kind == "object" and prop.get("properties"):
        inner = ", ".join(f"{k}: {_type_of(_resolve(v, defs), defs)}" for k, v in prop["properties"].items())
        return "{" + inner + "}"

    return str(kind)
