"""Runner engine against a fake main: step order, references, foreach, errors and empty
results; a run goes start to finish and never waits for anyone."""

from __future__ import annotations

from typing import Any

import pytest

from common.storage.db import session
from common.storage.models import Run, StepRun, Task, TaskVersion
from runner.engine import RunEngine
from tests.conftest import FakeMain


def _make_run(spec: dict[str, Any], *, dry_run: bool = False, task_settings: dict[str, Any] | None = None) -> str:
    with session() as db:
        db.add(Task(id="task_1", title=spec["title"], status="active", current_version_id="ver_1", settings=task_settings or {}))
        db.add(TaskVersion(id="ver_1", task_id="task_1", version_no=1, spec=spec, status="approved"))
        db.add(Run(id="run_1", task_id="task_1", task_version_id="ver_1", status="queued", dry_run=dry_run, state={}))
        db.commit()

    return "run_1"


SPEC = {
    "title": "Сортировка",
    "steps": [
        {"id": "files", "tool": "fs.list", "args": {"folder": "Входящие"}},
        {
            "id": "sort",
            "foreach": "{{ files.output.files }}",
            "as": "file",
            "steps": [
                {"id": "kind", "tool": "llm.classify", "args": {"input": "{{ file.path }}", "labels": ["счёт", "акт"]}},
                {"id": "move", "tool": "fs.move", "args": {"src": "{{ file.path }}", "dst": "Документы/{{ kind.output.label }}/"}},
            ],
        },
    ],
}


def _tools():
    return {
        "fs.list": lambda args, ctx: {"output": {"files": [{"path": "Входящие/a.txt"}, {"path": "Входящие/b.txt"}]}},
        "llm.classify": lambda args, ctx: {"output": {"label": "счёт" if args["input"].endswith("a.txt") else "акт", "reason": ""}},
        "fs.move": lambda args, ctx: {"output": {"path": args["dst"]}},
    }


async def test_foreach_passes_outputs_between_steps(settings):
    main = FakeMain(_tools())
    run = await RunEngine(main).execute(_make_run(SPEC))

    assert run["status"] == "succeeded"

    moves = [c["args"]["dst"] for c in main.calls if c["tool"] == "fs.move"]
    assert moves == ["Документы/счёт/", "Документы/акт/"]

    # Every call carries the run mode and the run's identity.
    assert all(c["context"]["mode"] == "run" and c["context"]["run_id"] == "run_1" for c in main.calls)
    assert [e[0] for e in main.events] == ["run.started", "run.finished"]


def test_tasks_cannot_ask_the_user():
    from pydantic import ValidationError

    from common.schemas.task import TaskSpec

    for policy in ({"on_error": "ask_user"}, {"on_empty": "ask_user"}):
        with pytest.raises(ValidationError):
            TaskSpec.model_validate({"title": "t", "steps": [{"id": "a", "tool": "fs.list", **policy}]})


async def test_external_actions_never_pause_a_run(settings):
    spec = {"title": "Письмо", "steps": [{"id": "send", "tool": "mail.send", "args": {}}]}
    main = FakeMain({"mail.send": lambda args, ctx: {"output": {"mode": "draft"}}})

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "succeeded"
    assert [e[0] for e in main.events] == ["run.started", "run.finished"]


async def test_empty_result_with_skip_stops_quietly(settings):
    spec = {"title": "Пусто", "steps": [{"id": "files", "tool": "fs.list", "args": {}, "on_empty": "skip"}, {"id": "never", "tool": "fs.list", "args": {}}]}
    main = FakeMain({"fs.list": lambda args, ctx: {"output": {"files": []}}})

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "empty"
    assert len(main.calls) == 1


async def test_error_with_retries_then_fail(settings, monkeypatch):
    monkeypatch.setattr("runner.engine._RETRY_BACKOFF_SEC", 0)
    spec = {"title": "Сбой", "steps": [{"id": "boom", "tool": "fs.list", "args": {}, "on_error": "retry", "retries": 2}]}
    main = FakeMain({"fs.list": lambda args, ctx: {"status": "error", "error": "нет папки"}})

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "failed"
    assert "нет папки" in run["error"]
    assert len(main.calls) == 3

    with session() as db:
        assert db.query(StepRun).filter(StepRun.status == "error").count() == 3


async def test_steps_carry_the_task_settings(settings):
    main = FakeMain(_tools())
    await RunEngine(main).execute(_make_run(SPEC, task_settings={"mail": {"send_directly": True}}))

    assert all(c["context"]["task_settings"] == {"mail": {"send_directly": True}} for c in main.calls)


CHECK = {
    "title": "Заявки",
    "steps": [
        {"id": "found", "tool": "mail.search", "args": {}},
        {"id": "reply", "foreach": "{{ found.output.messages }}", "as": "msg", "steps": [{"id": "send", "tool": "mail.send", "args": {}}]},
    ],
}


async def test_check_that_found_nothing_is_an_empty_run(settings):
    main = FakeMain({"mail.search": lambda args, ctx: {"output": {"messages": []}}})

    run = await RunEngine(main).execute(_make_run(CHECK))

    assert run["status"] == "empty"


async def test_check_that_found_something_succeeds(settings):
    main = FakeMain({
        "mail.search": lambda args, ctx: {"output": {"messages": [{"uid": 1}]}},
        "mail.send": lambda args, ctx: {"output": {"mode": "sent"}},
    })

    run = await RunEngine(main).execute(_make_run(CHECK))

    assert run["status"] == "succeeded"


async def test_dry_run_is_never_empty(settings):
    main = FakeMain({"mail.search": lambda args, ctx: {"output": {"messages": []}}})

    run = await RunEngine(main).execute(_make_run(CHECK, dry_run=True))

    assert run["status"] == "succeeded"
