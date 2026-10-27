"""A task version keeps the definition of each tool it calls: a tool that is another now
does not run in the task until its user approves a version with the new definition."""

from __future__ import annotations

import copy

import pytest

from common.errors import ApiError
from common.external_tools import store as tools
from common.schemas.execution import ExecContext, ExecuteRequest
from common.schemas.task import TaskSpec
from common.storage.db import session
from common.storage.models import Run, Task, TaskVersion
from common.tools import discover
from common.tools.definition import changed, changes, definition_hash, snapshot
from compiler import store
from compiler.compile import Compiler
from runner.engine import RunEngine
from tests.conftest import FakeMain, tool_hash
from tests.test_external_tools import HAVE_UV, MANIFEST


INTERNAL = {name: spec.manifest() for name, spec in discover().items()}


def _manifest(properties: dict, required: list[str], **more) -> dict:
    manifest = {
        "name": "demo.count", "function_name": "demo__count", "title": "Посчитать", "card": "Посчитать «{file}»",
        "description": "…", "confirm": False, "secrets": [], "timeout": None, "settings_schema": None, "guide": None,
        "core": False, "source": "external", "code_hash": "code-1",
        "input_schema": {"type": "object", "properties": properties, "required": required},
        "output_schema": {"type": "object", "properties": {"lines": {"type": "integer"}}},
        **more,
    }
    return {**manifest, "hash": definition_hash(manifest)}


V1 = _manifest({"file": {"type": "string"}}, ["file"])
NEW_CODE = _manifest({"file": {"type": "string"}}, ["file"], code_hash="code-2")
NEW_ARGS = _manifest({"path": {"type": "string"}}, ["path"])

SPEC = {"title": "Строки", "steps": [{"id": "count", "tool": "demo.count", "args": {"file": "a.txt"}}]}


class _Main:
    def __init__(self, current: dict):
        self.current = current

    async def tools(self):
        return {**INTERNAL, self.current["name"]: self.current}


def _task(manifest: dict = V1, spec: dict = SPEC) -> str:
    checked = TaskSpec.model_validate(spec)
    catalog = {**INTERNAL, "demo.count": manifest}

    return store.create_task(checked, session_id=None, cards=[], summary="", settings={}, tools=snapshot(checked.tools(), catalog)).task_id


def _approve_first(task_id: str, manifest: dict = V1) -> None:
    store.approve(task_id, store.base_version(task_id).id, {**INTERNAL, "demo.count": manifest})


def test_the_hash_is_what_the_call_is_not_how_it_is_shown():
    assert definition_hash({**V1, "card": "Другая карточка", "guide": "…", "core": True, "title": "Иначе"}) == V1["hash"]
    assert NEW_CODE["hash"] != V1["hash"] and NEW_ARGS["hash"] != V1["hash"]

    # Built-in manifests carry theirs; an own command's includes its code.
    assert INTERNAL["fs.list"]["hash"] == definition_hash(INTERNAL["fs.list"])


def test_a_tool_is_changed_when_its_hash_differs_it_is_gone_or_was_never_kept():
    kept = snapshot(["demo.count"], {"demo.count": V1})
    catalog = {"demo.count": V1}

    assert changed(kept, ["demo.count"], catalog) == []
    assert changed(kept, ["demo.count"], {"demo.count": NEW_CODE}) == ["demo.count"]
    assert changed(kept, ["demo.count"], {}) == ["demo.count"]
    assert changed({"demo.count": {}}, ["demo.count"], catalog) == ["demo.count"]

    assert changes(kept, {"demo.count": NEW_ARGS}) == [{"tool": "demo.count", "fields": ["аргументы"]}]
    assert changes(kept, {}) == [{"tool": "demo.count", "fields": ["команды больше нет"]}]


async def test_a_draft_is_approved_only_with_the_tools_it_shows(settings):
    task_id = _task()

    with pytest.raises(ApiError) as e:
        store.approve(task_id, store.base_version(task_id).id, {**INTERNAL, "demo.count": NEW_CODE})

    assert e.value.status_code == 409 and e.value.details == {"tools_changed": ["demo.count"]}


async def test_refreshing_shows_what_changed_and_keeps_the_steps(settings):
    task_id = _task()
    _approve_first(task_id)

    result = await Compiler(settings, llm=None, main=_Main(NEW_CODE)).refresh_tools(task_id)
    draft = store.get_version(task_id, result["version_id"])

    assert result["diff"] == ["~ изменилась команда `demo.count`: код"]
    assert draft.created_by == "tools" and draft.spec == store.get_version(task_id, draft.parent_version_id).spec
    assert draft.tools["demo.count"]["hash"] == NEW_CODE["hash"]

    # With it the task is approved again, and the kept definition is the new one.
    store.approve(task_id, draft.id, {**INTERNAL, "demo.count": NEW_CODE})


async def test_refreshing_refuses_when_nothing_changed_or_the_steps_no_longer_fit(settings):
    task_id = _task()
    _approve_first(task_id)

    with pytest.raises(ApiError, match="не менялись"):
        await Compiler(settings, llm=None, main=_Main(V1)).refresh_tools(task_id)

    with pytest.raises(ApiError) as e:
        await Compiler(settings, llm=None, main=_Main(NEW_ARGS)).refresh_tools(task_id)

    assert "поправьте задачу словами" in e.value.message
    assert any("path" in error for error in e.value.details["errors"])


async def test_any_edit_keeps_the_tools_as_they_are_and_says_what_changed(settings):
    task_id = _task()
    _approve_first(task_id)

    result = await Compiler(settings, llm=None, main=_Main(NEW_CODE)).edit(
        task_id, spec=None, ops=[{"op": "replace", "path": "/title", "value": "Строки, иначе"}], summary=None
    )

    assert "~ изменилась команда `demo.count`: код" in result["diff"]
    assert store.get_version(task_id, result["version_id"]).tools["demo.count"]["hash"] == NEW_CODE["hash"]


def _run(spec: dict, kept: dict, *, dry_run: bool = False) -> str:
    with session() as db:
        db.add(Task(id="task_1", title=spec["title"], status="active", current_version_id="ver_1"))
        db.add(TaskVersion(id="ver_1", task_id="task_1", version_no=1, spec=spec, status="approved", tools=kept))
        db.add(Run(id="run_1", task_id="task_1", task_version_id="ver_1", status="queued", dry_run=dry_run))
        db.commit()

    return "run_1"


FILES = {"title": "Файлы", "steps": [{"id": "files", "tool": "fs.list", "args": {}}, {"id": "again", "tool": "fs.list", "args": {}}]}


async def test_a_run_whose_tools_changed_does_not_start_and_main_hears_of_it(settings):
    main = FakeMain({"fs.list": lambda args, ctx: {"output": {"files": []}}}, hashes={"fs.list": "another"})

    run = await RunEngine(main).execute(_run(FILES, {"fs.list": {"hash": tool_hash("fs.list")}}))

    assert run["status"] == "failed" and "fs.list" in run["error"]
    assert main.calls == []
    assert [(c["tools"], c["dry_run"]) for c in main.changed] == [(["fs.list"], False)]


async def test_a_version_that_kept_no_definitions_does_not_run(settings):
    main = FakeMain({"fs.list": lambda args, ctx: {"output": {"files": []}}})

    run = await RunEngine(main).execute(_run(FILES, {"fs.list": {}}))

    assert run["status"] == "failed" and main.calls == []


async def test_every_call_carries_the_kept_hash_and_a_change_midway_stops_the_run(settings):
    main = FakeMain({"fs.list": lambda args, ctx: {"output": {"files": ["a"]}}})
    calls = []

    async def call_tool(tool, args, *, hash=None, context=None):
        calls.append(hash)
        if len(calls) == 2:
            return {"status": "error", "error": "изменился", "tool_changed": True}

        return await FakeMain.call_tool(main, tool, args, hash=hash, context=context)

    main.call_tool = call_tool
    # on_error: skip does not hide it: a changed tool is never just a failed step.
    spec = copy.deepcopy(FILES)
    spec["steps"][1]["on_error"] = "skip"

    run = await RunEngine(main).execute(_run(spec, {"fs.list": {"hash": tool_hash("fs.list")}}))

    assert calls == [tool_hash("fs.list")] * 2
    assert run["status"] == "failed"
    assert main.changed[0]["tools"] == ["fs.list"]


async def test_the_executor_refuses_a_call_for_another_definition(settings):
    from executor.registry import ToolRegistry
    from executor.runtime import ExecutorRuntime

    registry = ToolRegistry(settings.storage)
    registry.load()
    runtime = ExecutorRuntime(settings, registry, secrets=None)

    def request(tool, value):
        return ExecuteRequest(tool=tool, hash=value, args={"folder": "."}, context=ExecContext(mode="run", run_id="run_t"))

    stale = await runtime.execute(request("fs.list", "another"))
    gone = await runtime.execute(request("demo.gone", "another"))
    right = await runtime.execute(request("fs.list", registry.manifest("fs.list")["hash"]))

    assert stale.tool_changed and "изменился" in stale.error
    assert gone.tool_changed
    assert right.status == "ok" and not right.tool_changed


@pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")
def test_new_code_of_an_own_command_is_another_definition(settings):
    from executor.registry import ToolRegistry

    registry = ToolRegistry(settings.storage)

    hashes = []
    for marker in ("первая", "вторая"):
        code = f"def run(args, ctx):\n    return {{'lines': 0, 'out': '{marker}'}}\n"
        draft = tools.save_draft(settings.storage, "demo.count_lines", manifest=MANIFEST, files={"main.py": code}, requirements=[])
        tools.approve(settings.storage, "demo.count_lines", draft["hash"])
        registry.load()
        hashes.append(registry.manifest("demo.count_lines")["hash"])

    assert hashes[0] != hashes[1]


def test_the_task_view_tells_what_changed_in_its_tools(settings):
    from main import views

    task_id = _task()
    _approve_first(task_id)

    assert views.task_detail(task_id, {**INTERNAL, "demo.count": V1})["tool_changes"] == []
    assert views.task_detail(task_id, {**INTERNAL, "demo.count": NEW_CODE})["tool_changes"] == [{"tool": "demo.count", "fields": ["код"]}]
    # Without the executor's word nothing is claimed.
    assert views.task_detail(task_id, None)["tool_changes"] == []


def test_tasks_can_be_listed_by_the_tool_they_use(settings):
    from main import views

    on_tool = _task()
    _task(spec={"title": "Другое", "steps": [{"id": "files", "tool": "fs.list", "args": {"folder": "."}}]})

    assert [task["id"] for task in views.task_list(tool="demo.count")] == [on_tool]
