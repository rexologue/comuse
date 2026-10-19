"""Runner engine against a fake main: step order, references, foreach, pausing for an
approval and resuming from the saved state."""

from __future__ import annotations

from typing import Any

import pytest

from common.storage.db import session
from common.storage.models import Run, StepRun, Task, TaskVersion
from runner.engine import RunEngine
from tests.conftest import FakeMain


def _make_run(spec: dict[str, Any], *, dry_run: bool = False, send_mode: str = "draft") -> str:
    with session() as db:
        db.add(Task(id="task_1", title=spec["title"], status="active", current_version_id="ver_1", send_mode=send_mode))
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

    # Every call carries the task's permissions and the run's identity.
    assert all(c["context"]["mode"] == "run" and c["context"]["run_id"] == "run_1" for c in main.calls)
    assert [e[0] for e in main.events] == ["run.started", "run.finished"]


async def test_pause_for_approval_and_resume(settings):
    spec = {
        "title": "Письмо",
        "steps": [
            {"id": "note", "tool": "llm.write", "args": {"instruction": "x"}},
            {"id": "send", "tool": "mail.send", "args": {"body": "{{ note.output.text }}"}},
        ],
    }

    def send(args, ctx):
        if ctx["confirmed"]:
            return {"output": {"mode": "sent", "body": args["body"]}}

        return {"status": "needs_approval", "output": {"mode": "draft"}, "message": "нужно подтверждение"}

    main = FakeMain({"llm.write": lambda args, ctx: {"output": {"text": "Привет"}}, "mail.send": send})
    engine = RunEngine(main)

    paused = await engine.execute(_make_run(spec))
    assert paused["status"] == "waiting"
    assert main.approvals[0]["kind"] == "confirm_action"

    engine.resume("run_1", main.approvals[0]["id"], approve=True, answer=None)
    finished = await engine.execute("run_1")

    assert finished["status"] == "succeeded"
    assert finished["result"]["outputs"] == {"mode": "sent", "body": "Привет"}

    # The completed first step is not executed again after the resume.
    assert [c["tool"] for c in main.calls].count("llm.write") == 1


async def test_declined_action_skips_the_step(settings):
    spec = {"title": "Письмо", "steps": [{"id": "send", "tool": "mail.send", "args": {}}]}
    main = FakeMain({"mail.send": lambda args, ctx: {"status": "needs_approval"}})
    engine = RunEngine(main)

    await engine.execute(_make_run(spec))
    engine.resume("run_1", main.approvals[0]["id"], approve=False, answer=None)
    finished = await engine.execute("run_1")

    assert finished["status"] == "succeeded"
    assert [s["status"] for s in finished["steps"]][-1] == "skipped"


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


async def test_steps_carry_the_task_send_mode(settings):
    main = FakeMain(_tools())
    await RunEngine(main).execute(_make_run(SPEC, send_mode="send"))

    assert {c["context"]["send_mode"] for c in main.calls} == {"send"}


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
