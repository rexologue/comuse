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


async def test_each_step_carries_its_own_settings(settings):
    """The step's value, else the task's; the executor fills in the defaults."""
    main = FakeMain(_tools())
    task_settings = {"fs": {"fs.move": {"overwrite": {"all": False, "steps": {"move": True}}, "keep": {"all": 1}}}}

    await RunEngine(main).execute(_make_run(SPEC, task_settings=task_settings))

    by_tool = {c["tool"]: c["context"]["tool_settings"] for c in main.calls}
    assert by_tool["fs.move"] == {"overwrite": True, "keep": 1}
    assert by_tool["fs.list"] == {}


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


def test_retention_removes_old_runs_with_their_log(settings):
    from datetime import timedelta

    from common.storage.models import RunLog
    from common.utils import utcnow
    from runner.retention import sweep

    with session() as db:
        db.add(Run(id="run_old", task_id="t", task_version_id="v", status="succeeded", finished_at=utcnow() - timedelta(days=30)))
        db.add(Run(id="run_new", task_id="t", task_version_id="v", status="succeeded", finished_at=utcnow() - timedelta(days=1)))
        db.add(RunLog(run_id="run_old", task_id="t", seq=1, kind="start", data={}))
        db.commit()

    assert sweep() == 1

    with session() as db:
        assert [r.id for r in db.query(Run)] == ["run_new"]
        assert db.query(RunLog).count() == 0


def test_old_output_folders_go_whoever_made_them(settings):
    """Runs' and chats' files alike: a folder nothing changed in for `retention_days`."""
    import os
    import time

    from executor.outputs import sweep

    outputs = settings.storage.outputs_dir
    for name in ("run_old", "ses_old", "run_new"):
        (outputs / name).mkdir(parents=True)
        (outputs / name / "report.docx").write_text("x")

    month_ago = time.time() - 30 * 86400
    for name in ("run_old", "ses_old"):
        for path in (outputs / name / "report.docx", outputs / name):
            os.utime(path, (month_ago, month_ago))

    assert sweep(settings.storage) == 2
    assert sorted(p.name for p in outputs.iterdir()) == ["run_new"]


def test_old_events_go(settings):
    from datetime import timedelta

    from common.storage.models import Event
    from common.utils import utcnow
    from main import events

    with session() as db:
        db.add(Event(type="run.finished", payload={}, created_at=utcnow() - timedelta(days=30)))
        db.add(Event(type="run.finished", payload={}))
        db.commit()

    assert events.sweep() == 1
    assert len(events.recent()) == 1


def test_the_newest_event_stays_so_ids_never_go_back(settings):
    """A client resumes from the last id it saw: an emptied table would start over at 1."""
    from datetime import timedelta

    from common.storage.models import Event
    from common.utils import utcnow
    from main import events

    with session() as db:
        for _ in range(3):
            db.add(Event(type="run.finished", payload={}, created_at=utcnow() - timedelta(days=30)))
        db.commit()

    assert events.sweep() == 2
    newest = events.recent()[-1]["id"]

    with session() as db:
        db.add(Event(type="run.finished", payload={}))
        db.commit()

    assert events.recent(since=newest)[0]["id"] == newest + 1


async def test_a_service_on_a_new_address_gets_a_new_client(settings):
    from main.registry import ServiceRegistry

    registry = ServiceRegistry(internal_token="")
    registry.register("executor", "http://127.0.0.1:9001")
    first = registry.client("executor")
    assert registry.client("executor") is first

    registry.register("executor", "http://127.0.0.1:9002")
    assert registry.client("executor").base_url == "http://127.0.0.1:9002"


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


async def test_a_run_its_caller_stopped_waiting_for_is_closed(settings):
    """wait=true gives up on a long run: the run must end as failed, not stay running."""
    import asyncio

    class SlowMain(FakeMain):
        async def post(self, path, json=None, **kwargs):
            if path == "/executor/execute":
                await asyncio.sleep(10)

            return await super().post(path, json=json, **kwargs)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(RunEngine(SlowMain(_tools())).execute(_make_run(SPEC, dry_run=True)), timeout=0.05)

    with session() as db:
        run = db.get(Run, "run_1")

    assert run.status == "failed" and run.finished_at is not None
    assert run_log("run_1")[-1]["kind"] == "end"


async def test_a_run_cancelled_in_the_queue_is_closed_like_any_other(settings):
    """It has no coroutine to stop: closed right away, with an end in its log and an event,
    so retention can sweep it like any finished run."""
    from runner.api.runs import cancel_run
    from types import SimpleNamespace

    main = FakeMain(_tools())
    engine = RunEngine(main)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(engine=engine)))

    answer = await cancel_run(_make_run(SPEC), request)

    with session() as db:
        run = db.get(Run, "run_1")

    assert answer == {"run_id": "run_1", "status": "cancelled"}
    assert run.status == "cancelled" and run.finished_at is not None
    assert [e["kind"] for e in run_log("run_1")] == ["end"]
    assert [kind for kind, _ in main.events] == ["run.finished"]

    # The queued run's coroutine, when it gets its turn, finds it closed and does nothing.
    await engine.execute("run_1")
    assert main.calls == [] and len(run_log("run_1")) == 1


async def test_a_step_its_when_left_out_does_not_hide_an_empty_check(settings):
    spec = {
        "title": "Проверка почты",
        "steps": [
            {"id": "found", "tool": "mail.search", "args": {"account": "work"}},
            {"id": "reply", "tool": "mail.send", "when": "{{ found.output.messages }}", "args": {"account": "work", "to": ["a@company.local"], "subject": "s", "body": "b"}},
        ],
    }
    main = FakeMain({"mail.search": lambda args, ctx: {"output": {"messages": []}}})

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "empty"


async def test_a_version_that_does_not_read_fails_instead_of_staying_queued(settings):
    run = await RunEngine(FakeMain({})).execute(_make_run({"title": "Старая", "steps": [{"id": "x"}]}))

    assert run["status"] == "failed" and "не читается" in run["error"]
    assert [entry["kind"] for entry in run_log("run_1")] == ["end"]


async def test_a_step_that_ran_out_of_time_is_not_retried(settings, monkeypatch):
    """Whether it sent the letter is unknown: a retry could send it twice."""
    monkeypatch.setattr("runner.engine._RETRY_BACKOFF_SEC", 0)
    spec = {"title": "Письмо", "steps": [{"id": "send", "tool": "mail.send", "args": {}, "on_error": "retry", "retries": 3}]}
    main = FakeMain({"mail.send": lambda args, ctx: {"status": "error", "error": "не уложился в таймаут", "timed_out": True}})

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "failed" and len(main.calls) == 1


async def test_a_cancel_stops_the_retries(settings, monkeypatch):
    monkeypatch.setattr("runner.engine._RETRY_BACKOFF_SEC", 0)
    spec = {"title": "Сбой", "steps": [{"id": "boom", "tool": "fs.list", "args": {}, "on_error": "retry", "retries": 5}]}
    engine = None

    def fails(args, ctx):
        engine._cancelled.add("run_1")
        return {"status": "error", "error": "нет папки"}

    engine = RunEngine(FakeMain({"fs.list": fails}))
    run = await engine.execute(_make_run(spec))

    assert run["status"] == "cancelled" and len(engine.main.calls) == 1


async def test_foreach_skips_a_failed_item_and_knows_an_empty_list(settings):
    spec = {
        "title": "Каждый файл",
        "steps": [
            {"id": "files", "tool": "fs.list", "args": {}},
            {"id": "each", "foreach": "{{ files.output.files }}", "as": "file", "on_error": "skip", "on_empty": "fail",
             "steps": [{"id": "move", "tool": "fs.move", "args": {"src": "{{ file.path }}", "dst": "Архив/"}}]},
        ],
    }

    def move(args, ctx):
        return {"status": "error", "error": "занят"} if args["src"].endswith("a.txt") else {"output": {"path": "Архив/b.txt"}}

    main = FakeMain({**_tools(), "fs.move": move})
    run = await RunEngine(main).execute(_make_run(spec))
    assert run["status"] == "succeeded" and len(main.calls) == 3

    each = next(e for e in run_log("run_1") if e["kind"] == "step" and e["step_key"] == "each")
    assert [item.get("error") is not None for item in each["output"]] == [True, False]

    with session() as db:
        db.query(Run).delete()
        db.query(TaskVersion).delete()
        db.query(Task).delete()
        db.commit()

    empty = await RunEngine(FakeMain({"fs.list": lambda a, c: {"output": {"files": []}}})).execute(_make_run(spec))
    assert empty["status"] == "failed" and "ничего не вернул" in empty["error"]


def test_a_foreach_cannot_retry_as_a_whole():
    from common.schemas.task import TaskSpec

    with pytest.raises(ValueError, match="foreach cannot retry"):
        TaskSpec.model_validate({"title": "t", "steps": [{"id": "each", "foreach": "{{ x }}", "on_error": "retry", "steps": [{"id": "a", "tool": "fs.list"}]}]})


def test_an_archived_or_paused_task_is_not_run_behind_its_back(settings):
    from common.errors import ApiError
    from runner.api.runs import RunBody, _resolve

    _make_run(SPEC)

    for status, body in (
        ("archived", RunBody(task_version_id="ver_1")),             # by its version, not only by the task
        ("paused", RunBody(task_id="task_1", trigger="cron")),      # a pause the scheduler missed
    ):
        with session() as db:
            db.get(Task, "task_1").status = status
            db.commit()

        with pytest.raises(ApiError) as e:
            _resolve(body)
        assert e.value.status_code == 409

    # A dry run of an archived task's version is still allowed.
    assert _resolve(RunBody(task_version_id="ver_1", dry_run=True))[1].id == "ver_1"


def test_a_run_a_restart_cut_off_ends_its_log(settings):
    from runner.main import _close_interrupted_runs

    _make_run(SPEC)
    _close_interrupted_runs()

    assert [(e["kind"], e["status"]) for e in run_log("run_1")] == [("end", "failed")]
