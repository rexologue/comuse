from __future__ import annotations

import json
from datetime import date
from typing import Any


_EXAMPLE = {
    "schema_version": 1,
    "title": "Еженедельный отчёт по продажам",
    "description": "Собрать выгрузки за прошлую неделю, заполнить отчёт и отправить руководителю",
    "trigger": {"type": "cron", "expr": "0 10 * * MON"},
    "permissions": {"read_folders": ["Отчёты/Продажи"], "mail_accounts": ["work"], "mail_send_to": ["boss@company.ru"]},
    "approval": {"mode": "draft_first_n", "n": 3},
    "variables": {
        "week_start": "{{ today | start_of_prev_week }}",
        "week_end": "{{ today | end_of_prev_week }}",
    },
    "steps": [
        {
            "id": "files",
            "tool": "fs.list",
            "args": {
                "folder": "Отчёты/Продажи",
                "pattern": "*.xlsx",
                "modified_from": "{{ week_start }}",
                "modified_to": "{{ week_end }}",
            },
            "on_empty": "ask_user",
        },
        {
            "id": "data",
            "tool": "llm.extract",
            "args": {
                "inputs": "{{ files.output.files }}",
                "instruction": "Суммарная выручка и число заказов за неделю",
                "output_schema": {
                    "type": "object",
                    "properties": {"revenue": {"type": "number"}, "orders": {"type": "integer"}},
                    "required": ["revenue", "orders"],
                },
            },
        },
        {
            "id": "doc",
            "tool": "docs.fill_template",
            "args": {
                "template": "data:templates/weekly_sales.docx",
                "context": {
                    "period": "{{ week_start | format_date }} – {{ week_end | format_date }}",
                    "revenue": "{{ data.output.data.revenue }}",
                    "orders": "{{ data.output.data.orders }}",
                },
                "output_name": "Отчёт {{ week_start | format_date }}.docx",
            },
        },
        {
            "id": "send",
            "tool": "mail.send",
            "args": {
                "account": "work",
                "to": ["boss@company.ru"],
                "subject": "Отчёт по продажам за {{ week_start | format_date }} – {{ week_end | format_date }}",
                "body": "Добрый день! Отчёт во вложении.",
                "attachments": ["{{ doc.output.path }}"],
            },
            "on_error": "retry",
            "retries": 2,
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
3. Обобщай конкретику: даты — через variables и фильтры; конкретные файлы — шагом fs.list с pattern и датами.
4. Ссылка на результат шага: {{{{ <id_шага>.output.<поле> }}}} — поля бери из output-схемы инструмента.
   Строка, которая целиком является одним {{{{ … }}}}, подставляется как значение (список остаётся списком).
5. Доступно в шаблонах: today, now, variables, результаты шагов выше, переменная foreach (as). Фильтры дат:
   start_of_week, end_of_week, start_of_prev_week, end_of_prev_week, start_of_month, end_of_month,
   start_of_prev_month, end_of_prev_month, add_days(n), format_date('%d.%m.%Y').
6. Если в разговоре шаблон уже создан (docs.make_template), не создавай его заново — используй готовый путь data:templates/….
7. Для обработки каждого файла по отдельности — foreach с вложенными steps, например:
   {json.dumps(_FOREACH_EXAMPLE, ensure_ascii=False)}
8. Пути — только относительные (как в результатах fs.list) или data:…; никаких абсолютных путей.
9. trigger: cron из 5 полей (дни недели MON..SUN), file_appeared, new_email или manual — по просьбе пользователя.
10. permissions: перечисли папки, почтовые аккаунты и адресатов, которые использует задача.
11. id шагов — латиницей snake_case, короткие и понятные.

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

    return [{"role": "system", "content": RULES}, {"role": "user", "content": user}]


def patch_messages(
    *,
    spec: dict[str, Any],
    instruction: str,
    catalog: dict[str, dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    system = (
        RULES
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


def repair_request(dry_run: dict[str, Any]) -> str:
    failed = [step for step in dry_run.get("steps", []) if step.get("status") == "error"]
    details = "\n".join(f"- шаг {step['step']}: {step['error']}" for step in failed[-3:])

    return (
        "Пробный запуск этой задачи на реальных данных упал.\n"
        f"Ошибка: {dry_run.get('error')}\n{details}\n\n"
        "Исправь задачу так, чтобы она работала. Помни: файлы ищутся шагом fs.list (с pattern и датами), "
        "а не собираются из имён; ссылки на результаты — только на поля из output-схем. "
        "Верни задачу целиком, только JSON."
    )


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
            f"- {name} [{manifest['side_effect']}]: {manifest['description']}\n"
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
