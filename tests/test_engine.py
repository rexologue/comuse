"""Runner engine against a fake main: step order, references, foreach, errors and empty
results; a run goes start to finish and never waits for anyone."""

from __future__ import annotations

from typing import Any

import pytest

from common.storage.db import session
from common.storage.models import Run, Task, TaskVersion
from common.storage.queries import run_log
from runner.engine import RunEngine
from tests.conftest import FakeMain


def _make_run(spec: dict[str, Any], *, dry_run: bool = False, task_settings: dict[str, Any] | None = None) -> str:
    with session() as db:
        db.add(Task(id="task_1", title=spec["title"], status="active", current_version_id="ver_1", settings=task_settings or {}))
        db.add(TaskVersion(id="ver_1", task_id="task_1", version_no=1, spec=spec, status="approved"))
        db.add(Run(id="run_1", task_id="task_1", task_version_id="ver_1", status="queued", dry_run=dry_run))
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

    errors = [e for e in run_log("run_1") if e["kind"] == "step" and e["status"] == "error"]
    assert [e["attempt"] for e in errors] == [1, 2, 3]


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


async def test_one_log_holds_the_whole_run(settings):
    tools = _tools()
    tools["fs.list"] = lambda args, ctx: {"output": {"files": [{"path": "Входящие/a.txt"}]}, "logs": ["нашёл 1 файл"], "duration_ms": 7}
    main = FakeMain(tools)

    await RunEngine(main).execute(_make_run({**SPEC, "variables": {"day": "{{ today }}"}}))

    log = run_log("run_1")
    assert [e["seq"] for e in log] == list(range(1, len(log) + 1))
    assert log[0]["kind"] == "start" and "day" in log[0]["variables"]
    assert log[-1]["kind"] == "end" and log[-1]["status"] == "succeeded"

    files = next(e for e in log if e.get("step_key") == "files")
    assert files["tool"] == "fs.list" and files["logs"] == ["нашёл 1 файл"] and files["duration_ms"] == 7


async def test_crash_traceback_lands_in_the_log(settings):
    spec = {"title": "Сбой", "steps": [{"id": "boom", "tool": "fs.list", "args": {}}]}
    main = FakeMain({"fs.list": lambda args, ctx: {"status": "error", "error": "Сбой", "traceback": "Traceback ...ZeroDivisionError"}})

    await RunEngine(main).execute(_make_run(spec))

    step = next(e for e in run_log("run_1") if e["kind"] == "step")
    assert "ZeroDivisionError" in step["traceback"]


async def test_notifications_go_to_the_log_and_the_tasks_chat(settings):
    spec = {"title": "Отчёт", "steps": [{"id": "done", "tool": "user.notify", "args": {"message": "готово"}}]}
    main = FakeMain({"user.notify": lambda args, ctx: {"output": {"sent": True}, "notifications": [args["message"]]}})

    await RunEngine(main).execute(_make_run(spec))

    assert [e["message"] for e in run_log("run_1") if e["kind"] == "notify"] == ["готово"]
    assert main.notified == [{"task_id": "task_1", "run_id": "run_1", "title": "Отчёт", "message": "готово"}]


async def test_dry_run_notifications_stay_in_the_log(settings):
    spec = {"title": "Отчёт", "steps": [{"id": "done", "tool": "user.notify", "args": {"message": "готово"}}]}
    main = FakeMain({"user.notify": lambda args, ctx: {"output": {"sent": True}, "notifications": [args["message"]]}})

    await RunEngine(main).execute(_make_run(spec, dry_run=True))

    assert [e["kind"] for e in run_log("run_1")].count("notify") == 1
    assert main.notified == []


def test_retention_removes_old_runs_with_their_log_and_files(settings):
    from datetime import timedelta

    from common.storage.models import RunLog
    from common.utils import utcnow
    from runner.retention import sweep

    with session() as db:
        db.add(Run(id="run_old", task_id="t", task_version_id="v", status="succeeded", finished_at=utcnow() - timedelta(days=30)))
        db.add(Run(id="run_new", task_id="t", task_version_id="v", status="succeeded", finished_at=utcnow() - timedelta(days=1)))
        db.add(RunLog(run_id="run_old", task_id="t", seq=1, kind="start", data={}))
        db.commit()

    outputs = settings.storage.outputs_dir / "run_old"
    outputs.mkdir(parents=True)
    (outputs / "report.docx").write_text("x")

    assert sweep(settings) == 1

    with session() as db:
        assert [r.id for r in db.query(Run)] == ["run_new"]
        assert db.query(RunLog).count() == 0

    assert not outputs.exists()


async def test_runs_endpoint_creates_and_executes_a_run(settings):
    from types import SimpleNamespace

    from runner.api.runs import RunBody, start_run

    _make_run(SPEC)  # the task and its version; the endpoint makes its own run
    with session() as db:
        db.query(Run).delete()
        db.commit()
    engine = RunEngine(FakeMain(_tools()))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine, background=set())))

    run = await start_run(RunBody(task_id="task_1", wait=True), request)

    assert run["status"] == "succeeded"
    assert run_log(run["id"])[-1]["kind"] == "end"
