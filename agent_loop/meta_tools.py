"""Tools that manage the system itself (tasks, approvals) rather than the computer.
They never reach the executor: agent-loop calls main directly."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from common.clients.main_client import MainClient
from common.errors import ApiError
from common.utils import truncate


Handler = Callable[[MainClient, str, dict[str, Any]], Awaitable[Any]]


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


_STR = {"type": "string"}

DEFINITIONS: list[dict[str, Any]] = [
    _fn(
        "task_compile",
        "Сохранить то, что сделано в этом чате, как повторяемую задачу (сценарий). Вызывай, когда "
        "пользователь просит делать это регулярно, по расписанию или «сформировать задачу». "
        "В hint передай расписание и пожелания пользователя своими словами.",
        {"title": _STR, "hint": _STR},
    ),
    _fn(
        "task_dry_run",
        "Пробный запуск черновика задачи по просьбе пользователя: шаги выполняются без настоящих "
        "внешних действий. Покажи пользователю результат как есть, включая ошибки.",
        {"task_id": _STR, "version_id": _STR},
        ["task_id", "version_id"],
    ),
    _fn(
        "task_reject",
        "Отклонить черновик задачи.",
        {"task_id": _STR, "version_id": _STR},
        ["task_id", "version_id"],
    ),
    _fn(
        "task_patch",
        "Изменить существующую задачу по просьбе пользователя (например, «добавь Васю в копию»). "
        "Создаёт черновик новой версии и показывает, что изменится.",
        {"task_id": _STR, "instruction": _STR},
        ["task_id", "instruction"],
    ),
    _fn("task_list", "Список задач пользователя с их шагами и расписанием.", {}),
    _fn("task_run_now", "Запустить задачу прямо сейчас.", {"task_id": _STR}, ["task_id"]),
    _fn(
        "task_control",
        "Приостановить, возобновить или убрать в архив задачу.",
        {"task_id": _STR, "action": {"type": "string", "enum": ["pause", "resume", "archive"]}},
        ["task_id", "action"],
    ),
    _fn("run_status", "Состояние запуска задачи: шаги, ошибки, результаты.", {"run_id": _STR}, ["run_id"]),
    _fn(
        "action_confirm",
        "Выполнить действие, которое ждало подтверждения (например, отправку письма), после явного согласия пользователя.",
        {"action_id": {"type": "integer"}},
        ["action_id"],
    ),
]

NAMES = {d["function"]["name"] for d in DEFINITIONS}


async def _compile(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    result = await main.post("/compiler/compile", json={"session_id": session_id, **args})

    return {
        "task_id": result["task_id"],
        "version_id": result["version_id"],
        "title": result.get("title"),
        "summary": result.get("summary"),
        "steps": result.get("cards"),
        "trigger": result.get("trigger"),
        "settings": [
            _setting_line(item)
            for items in (result.get("settings") or {}).values()
            for item in items.values()
        ],
        "next": (
            "Покажи пользователю шаги задачи простыми словами. Если есть настройки (settings), перескажи их "
            "как есть: каждая строка — текущее значение и что оно значит. Меняет их только сам пользователь в "
            "настройках задачи; ты их не меняешь и не обещаешь поменять. Дальше решает "
            "пользователь: попросит пробный запуск — вызови task_dry_run; попросит поправить — task_patch; "
            "включает задачу только он сам, в списке задач. Сам ты её не включаешь и не запускаешь без просьбы."
        ),
    }


def _setting_line(item: dict[str, Any]) -> str:
    """«Отправлять письма сразу: выключено. <what it means>» — a bare false next to the
    title reads as the opposite to a model."""
    value = item["value"]
    state = ("включено" if value else "выключено") if isinstance(value, bool) else str(value)

    return f"{item['title']}: {state}. {item['description']}".strip()


async def _dry_run(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    """Just run it and report: no repairs, the user judges the result."""
    run = await main.post("/runs", json={"task_version_id": args["version_id"], "dry_run": True, "wait": True})

    return {
        "run_id": run["id"],
        "status": run["status"],
        "error": run.get("error"),
        "steps": [
            {
                "step": step["step_key"],
                "status": step["status"],
                "error": step.get("error"),
                "output": truncate(step["output"], 600) if step.get("output") is not None else None,
            }
            for step in run.get("steps", [])
        ],
    }


async def _reject(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    await main.post(f"/tasks/{args['task_id']}/versions/{args['version_id']}/reject")

    return {"status": "rejected"}


async def _patch(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    result = await main.post("/compiler/patch", json={"session_id": session_id, **args})

    return {
        "task_id": result["task_id"],
        "version_id": result["version_id"],
        "summary": result.get("summary"),
        "diff": result.get("diff"),
        "next": (
            "Покажи пользователю, что изменится (было → стало). Попросит пробный запуск — task_dry_run "
            "с этим version_id. Новую версию включает только сам пользователь — в списке задач."
        ),
    }


async def _list(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    data = await main.get("/tasks")

    return [
        {"task_id": t["id"], "title": t["title"], "status": t["status"], "trigger": t["trigger"], "steps": t["cards"]}
        for t in data["tasks"]
    ]


async def _run_now(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    return await main.post("/runs", json={"task_id": args["task_id"]})


async def _control(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    return await main.post(f"/tasks/{args['task_id']}/{args['action']}")


async def _run_status(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    run = await main.get(f"/runs/{args['run_id']}")

    return {
        "status": run["status"],
        "error": run["error"],
        "result": run["result"],
        "steps": [{"step": s["step_key"], "status": s["status"], "error": s.get("error")} for s in run["steps"]],
    }


HANDLERS: dict[str, Handler] = {
    "task_compile": _compile,
    "task_dry_run": _dry_run,
    "task_reject": _reject,
    "task_patch": _patch,
    "task_list": _list,
    "task_run_now": _run_now,
    "task_control": _control,
    "run_status": _run_status,
}


async def call(main: MainClient, session_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run a meta tool; errors become a result the model can explain to the user."""
    try:
        output = await HANDLERS[name](main, session_id, args)
        return {"status": "ok", "output": output}

    except KeyError as e:
        return {"status": "error", "error": f"Не хватает аргумента {e}"}

    except ApiError as e:
        return {"status": "error", "error": e.message, "details": truncate(e.details or {}, 1500)}
