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

# Turning a task on and sending what a task run drafted are the user's decisions, made
# outside the chat; the agent only relays answers to questions.
_AGENT_ANSWERS = {"ask_user"}


DEFINITIONS: list[dict[str, Any]] = [
    _fn(
        "task_compile",
        "Сохранить то, что сделано в этом чате, как повторяемую задачу (сценарий). Вызывай, когда "
        "пользователь просит делать это регулярно, по расписанию или «сформировать задачу». "
        "В hint передай расписание и пожелания пользователя своими словами.",
        {"title": _STR, "hint": _STR},
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
    _fn("approvals_list", "Вопросы задач, которые ждут ответа пользователя.", {}),
    _fn(
        "approval_answer",
        "Передать ответ пользователя на вопрос задачи из approvals_list.",
        {"approval_id": _STR, "approve": {"type": "boolean"}, "answer": _STR},
        ["approval_id", "approve"],
    ),
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
        "dry_run": result.get("dry_run"),
        "next": (
            "Покажи пользователю шаги и результат пробного запуска простыми словами. Задача сохранена "
            "черновиком; включает её только сам пользователь — в списке задач. Сам ты её не включаешь."
        ),
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
            "Покажи пользователю, что изменится (было → стало). Новую версию включает только сам "
            "пользователь — в списке задач. Сам ты её не включаешь."
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
        "steps": [{"step": s["step_key"], "status": s["status"], "error": s["error"]} for s in run["steps"]],
    }


async def _approvals(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    data = await main.get("/approvals", params={"status": "pending"})

    return [
        {"approval_id": a["id"], "kind": a["kind"], "details": a["payload"]}
        for a in data["approvals"]
        if a["kind"] in _AGENT_ANSWERS
    ]


async def _answer(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    data = await main.get("/approvals", params={"status": "pending"})
    approval = next((a for a in data["approvals"] if a["id"] == args["approval_id"]), None)

    if approval is None:
        raise ApiError(status_code=404, message=f"Нет ожидающего вопроса `{args['approval_id']}`")

    if approval["kind"] not in _AGENT_ANSWERS:
        raise ApiError(status_code=403, message="Это подтверждение пользователь даёт сам, не через чат")

    return await main.post(
        f"/approvals/{args['approval_id']}/answer",
        json={"approve": args.get("approve", True), "answer": args.get("answer")},
    )


HANDLERS: dict[str, Handler] = {
    "task_compile": _compile,
    "task_reject": _reject,
    "task_patch": _patch,
    "task_list": _list,
    "task_run_now": _run_now,
    "task_control": _control,
    "run_status": _run_status,
    "approvals_list": _approvals,
    "approval_answer": _answer,
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
