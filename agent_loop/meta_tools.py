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
    _fn("task_list", "Список задач пользователя: шаги, расписание словами, настройки и черновик, который ждёт одобрения.", {}),
    _fn(
        "task_runs",
        "Последние запуски задачи (новые сначала): run_id, когда, пробный или нет, чем закончился. Подробности — run_status.",
        {"task_id": _STR},
        ["task_id"],
    ),
    _fn("accounts_list", "Аккаунты пользователя (почтовые ящики и другие): имя для аргумента account, вид, адрес. Без паролей.", {}),
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
        {
            "action_id": {"type": "integer"},
            "remember": {
                "type": "boolean",
                "description": "Пользователь сам сказал больше не спрашивать про эту команду в этом чате",
            },
        },
        ["action_id"],
    ),
]

NAMES = {d["function"]["name"] for d in DEFINITIONS}

# What the user reads in the chat instead of the name (GET /agent/meta-tools).
TITLES = {
    "task_compile": "Собрать задачу",
    "task_dry_run": "Пробный запуск",
    "task_reject": "Отклонить черновик",
    "task_patch": "Изменить задачу",
    "task_list": "Список задач",
    "task_runs": "Запуски задачи",
    "accounts_list": "Аккаунты",
    "task_run_now": "Запустить задачу",
    "task_control": "Пауза, возобновление или архив задачи",
    "run_status": "Состояние запуска",
    "action_confirm": "Выполнить после согласия",
}

# What changes the user's tasks for real asks first, every time, like a command with
# confirm="always": text the model read (a letter, a document) must not start a run.
ASKING = {"task_run_now", "task_control"}


def manifest(name: str) -> dict[str, Any] | None:
    """A meta tool that asks first, shaped like a tool manifest for the chat's question."""
    if name not in ASKING:
        return None

    definition = next(d["function"] for d in DEFINITIONS if d["function"]["name"] == name)

    return {"name": f"meta:{name}", "title": TITLES[name], "confirm": "always", "input_schema": definition["parameters"]}


async def _compile(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    result = await main.post("/compiler/compile", json={**args, "session_id": session_id})

    return {
        "task_id": result["task_id"],
        "version_id": result["version_id"],
        "title": result.get("title"),
        "summary": result.get("summary"),
        "steps": result.get("cards"),
        "trigger": result.get("trigger"),
        # Lines the compiler wrote: «Отправлять письма сразу: выключено (по умолчанию). …».
        "settings": result.get("settings") or [],
        "next": (
            "Покажи пользователю шаги задачи простыми словами. Если есть настройки (settings), перескажи их "
            "как есть: каждая строка — текущее значение и что оно значит. Меняет их только сам пользователь в "
            "настройках задачи; ты их не меняешь и не обещаешь поменять. Дальше решает "
            "пользователь: попросит пробный запуск — вызови task_dry_run; попросит поправить — task_patch; "
            "включает задачу только он сам, в списке задач. Сам ты её не включаешь и не запускаешь без просьбы."
        ),
    }


async def _dry_run(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    """Just run it and report: no repairs, the user judges the result."""
    run = await main.post("/runs", json={"task_version_id": args["version_id"], "dry_run": True, "wait": True})

    return {
        "run_id": run["id"],
        "dry_run": True,
        "status": run["status"],
        "error": run.get("error"),
        "steps": [
            {
                "step": step["step_key"],
                "status": step["status"],
                "error": step.get("error"),
                # What a step would have done: a dry run's whole point.
                "logs": [truncate(line, 300) for line in (step.get("logs") or [])][:10],
                "output": truncate(step["output"], 600) if step.get("output") is not None else None,
            }
            for step in run.get("steps", [])
        ],
        "next": (
            "Это пробный запуск: ничего не изменено, что сделал бы каждый шаг — в его logs. Пересказывай как «сделал бы», "
            "не «сделал». Упавший шаг или пустой результат — так и скажи, с причиной; задачу без просьбы пользователя не правь."
        ),
    }


async def _reject(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    await main.post(f"/tasks/{args['task_id']}/versions/{args['version_id']}/reject")

    return {"status": "rejected"}


async def _patch(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    # The chat's own session, whatever the model put in args.
    result = await main.post("/compiler/patch", json={**args, "session_id": session_id})

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
    # A draft waiting for the user is part of what the task is now: the chat names it.
    drafts = {item["task_id"]: item for item in (await main.get("/approvals")).get("approvals", [])}

    # Steps by their card text: every task must fit the model's view, however many there
    # are — a list cut short would hide the one the user asked about.
    return [
        {
            "task_id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "schedule": t.get("schedule"),
            "steps": _step_texts(t["cards"]),
            "settings": t.get("settings") or {},
            "draft": (
                {"version_id": draft["version_id"], "summary": draft.get("summary"), "steps": _step_texts(draft.get("cards") or [])}
                if (draft := drafts.get(t["id"])) else None
            ),
        }
        for t in data["tasks"]
    ]


def _step_texts(cards: list[dict[str, Any]], depth: int = 0) -> list[str]:
    texts = []
    for card in cards:
        texts.append(("  " * depth) + card["text"])
        texts += _step_texts(card.get("children") or [], depth + 1)

    return texts


async def _runs(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    runs = (await main.get(f"/tasks/{args['task_id']}/runs?include_empty=true"))["runs"]

    return [
        {
            "run_id": run["id"],
            "status": run["status"],
            "dry_run": run.get("dry_run"),
            "trigger": run.get("trigger"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "error": run.get("error"),
        }
        for run in runs[:10]
    ]


async def _accounts(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    data = await main.get("/accounts")
    accounts = data.get("accounts", data) if isinstance(data, dict) else data

    return [{"name": a["name"], "kind": a["kind"], "address": (a.get("config") or {}).get("email")} for a in accounts]


async def _run_now(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    return await main.post("/runs", json={"task_id": args["task_id"]})


async def _control(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    return await main.post(f"/tasks/{args['task_id']}/{args['action']}")


async def _run_status(main: MainClient, session_id: str, args: dict[str, Any]) -> Any:
    run = await main.get(f"/runs/{args['run_id']}")

    return {
        "status": run["status"],
        "dry_run": run.get("dry_run"),
        "error": run["error"],
        "result": run["result"],
        # Finished ones: the step that runs now is not in the list until it ends.
        "steps": [{"step": s["step_key"], "status": s["status"], "error": s.get("error")} for s in run["steps"]],
    }


HANDLERS: dict[str, Handler] = {
    "task_compile": _compile,
    "task_dry_run": _dry_run,
    "task_reject": _reject,
    "task_patch": _patch,
    "task_list": _list,
    "task_runs": _runs,
    "accounts_list": _accounts,
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
