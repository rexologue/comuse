from __future__ import annotations

import json
from datetime import date
from typing import Any

from common import templating
from common.tools.guides import collect


# The mechanics only, on made-up tools: an example with real ones goes stale with them,
# and models copy example steps verbatim. How to use real tools is each group's guide.
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
            "tool": "example.find",
            "args": {"where": "Бухгалтерия/Командировки", "since": "{{ month_start }}", "until": "{{ month_end }}"},
            "on_empty": "skip",
        },
        {
            "id": "totals",
            "tool": "example.count",
            "args": {"items": "{{ reports.output.items }}", "by": "Сотрудник"},
        },
        {
            "id": "summary",
            "tool": "example.write",
            "args": {
                "month": "{{ month_start | format_date('%m.%Y') }}",
                "total": "{{ totals.output.total }}",
                "rows": "{{ totals.output.groups }}",
                "name": "Командировки {{ month_start | format_date('%m.%Y') }}",
            },
        },
    ],
}

_FOREACH_EXAMPLE = {
    "id": "sort",
    "foreach": "{{ reports.output.items }}",
    "as": "item",
    "steps": [
        {"id": "doc", "tool": "example.open", "args": {"what": "{{ item.path }}"}},
        {"id": "kind", "tool": "example.judge", "args": {"input": "{{ doc.output.text }}"}},
        {"id": "move", "tool": "example.put", "args": {"what": "{{ item.path }}", "where": "Документы/{{ kind.output.label }}/"}},
    ],
}


RULES = f"""Ты превращаешь разговор пользователя с ассистентом в повторяемую задачу — JSON по схеме TaskSpec.
Задача будет выполняться без LLM-планирования: шаги идут строго по порядку, аргументы подставляются из шаблонов.

Правила:
1. Используй только инструменты из каталога и только их аргументы (имена и типы — по input-схемам).
2. Повтори успешную последовательность действий из разговора. Неудачные попытки, разведку и вопросы — выброси.
   Не добавляй действий, которых не было в разговоре и о которых пользователь не просил (например, отправку
   писем). Вызов «НЕ выполнено: пользователь не подтвердил» пользователь не разрешил: в задачу его не включай,
   если потом он сам прямо об этом не попросил. Вызов tools.try {{name, args}} — это вызов команды name с этими
   аргументами (её пробовали в чате): в задаче — шаг именно этой командой, не другой похожей. Инструменты example.* в примерах вымышленные: они показывают только формат, а не состав шагов.
3. Обобщай конкретику: даты — через variables и фильтры, конкретные значения из разговора — шагами, которые
   найдут их при запуске (как именно — в инструкциях групп ниже). Но значения, которые пользователь сам задал
   (номер, клиент, сумма, адрес, текст), — это значения задачи: впиши их в аргументы как есть. У задачи нет ввода
   при запуске: «пользователь укажет при запуске» невозможно, пустая переменная даст пустой документ.
4. Ссылка на результат шага: {{{{ <id_шага>.output.<поле> }}}} — поля бери из output-схемы инструмента.
   Строка, которая целиком является одним {{{{ … }}}}, подставляется как значение (список остаётся списком).
5. Доступно в шаблонах: today, now, variables, результаты шагов выше, переменная foreach (as). Фильтры:
   {templating.describe_filters()}. Числа и даты в тексте выводи через format_number и format_date.
6. Для обработки каждого элемента по отдельности — foreach с вложенными steps, например:
   {json.dumps(_FOREACH_EXAMPLE, ensure_ascii=False)}
   Результат foreach — {{{{ <id>.output }}}}: список, по одному на элемент, вида {{<as>: элемент, <id вложенного
   шага>: его результат}} — результат вложенного шага лежит прямо под его id, без .output. Например, итог после
   примера выше: {{% for r in sort.output if not r.error %}}{{{{ r.item.name }}}} → {{{{ r.move.path }}}}{{% endfor %}}.
   Действия над каждым элементом по отдельности (переложить, отправить, сохранить) — с on_error: skip у foreach,
   если пользователь не просил иначе: один неудачный элемент (файл с таким именем уже есть) не должен оставлять
   необработанными остальные. У каждого элемента есть error: null у удачного; у неудачного — текст ошибки, а результаты его шагов — null: в итоге после
   foreach бери только удачные (if not r.error, как в примере). Пометка, что элемент обработан (метка письма,
   перенос в папку «Готово»), — последний шаг внутри того же foreach, после действия: тогда неудачный элемент
   не помечается и при следующем запуске будет сделан снова. Не ставь её одним шагом после foreach на всё сразу.
7. Пути — только относительные (как в результатах инструментов) или data:…; никаких абсолютных путей.
8. trigger: cron из 5 полей (дни недели только именами MON..SUN, не числами) или manual — по просьбе пользователя. Trigger задаёт
   только время запуска; что искать (письма, файлы), делают шаги задачи. Последний день месяца — last в поле дня
   (0 17 last * *), не 28-31. Расписание, которого cron не выразит (каждые 90 минут, один раз в определённый день),
   не подменяй похожим: сделай manual и напиши в description, что такое расписание задать нельзя.
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
        "Пути в ops — JSON Pointer по текущей задаче; шаг называй его id, а не номером: /steps/send_report/args/cc, "
        "вложенный в foreach — /steps/sort/steps/move/args/dst. "
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


def _resolve(prop: dict[str, Any] | bool, defs: dict[str, Any]) -> dict[str, Any]:
    # `true` in a schema a person or the chat wrote: any value.
    if not isinstance(prop, dict):
        return {}

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
