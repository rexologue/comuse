"""A task runs the version of an external tool it was approved with; only the user moves
it to another one, as a new task version."""

from __future__ import annotations

import copy

import pytest

from common.errors import ApiError
from common.external_tools import store as tools
from common.schemas.execution import ExecContext, ExecuteRequest
from common.schemas.task import TaskSpec
from common.storage.db import session
from common.storage.models import Task
from common.tools import discover
from compiler import store, versions
from compiler.compile import Compiler
from compiler.validate import SpecError, validate_spec
from tests.test_external_tools import HAVE_UV, MANIFEST


INTERNAL = {name: spec.manifest() for name, spec in discover().items()}


def _manifest(version: int, properties: dict, required: list[str]) -> dict:
    return {
        "name": "ext.count", "function_name": "ext__count", "title": "Посчитать", "card": "Посчитать «{file}»",
        "description": "…", "confirm": False, "secrets": [], "timeout": None, "settings_schema": None, "guide": None,
        "core": False, "source": "external", "version": version,
        "input_schema": {"type": "object", "properties": properties, "required": required},
        "output_schema": {"type": "object", "properties": {"lines": {"type": "integer"}}},
    }


V1 = _manifest(1, {"file": {"type": "string"}}, ["file"])
V2 = _manifest(2, {"file": {"type": "string"}, "sep": {"type": "string"}}, ["file"])
V3 = _manifest(3, {"path": {"type": "string"}}, ["path"])


class _Main:
    def __init__(self, current: dict):
        self.current = current

    async def get(self, path, params=None, **_):
        if path == "/tools":
            return {"tools": [*INTERNAL.values(), self.current]}

        found = {1: V1, 2: V2, 3: V3}.get((params or {}).get("version"))
        if path != "/tools/ext.count" or found is None:
            raise ApiError(status_code=404, message="not found")

        return found


def _spec(version: int | None = 1, **args) -> dict:
    step = {"id": "count", "tool": "ext.count", "args": {"file": "a.txt", **args}}
    if version is not None:
        step["tool_version"] = version

    return {"title": "Строки", "steps": [step]}


def _task(spec: dict) -> str:
    return store.create_task(TaskSpec.model_validate(spec), session_id=None, cards=[], summary="", settings={}).task_id


def test_the_model_never_picks_a_version():
    catalog = {**INTERNAL, "ext.count": V2}

    new = _spec(version=7)
    versions.pin(new, catalog, keep={}, explicit=False)
    kept = _spec(version=7)
    versions.pin(kept, catalog, keep={"ext.count": 1}, explicit=False)

    assert new["steps"][0]["tool_version"] == 2      # a new task: the current version
    assert kept["steps"][0]["tool_version"] == 1     # a patched task: what it had


def test_a_version_is_checked_against_its_own_schema():
    with pytest.raises(SpecError, match="нет версии v1"):
        validate_spec(_spec(1), {**INTERNAL, "ext.count": V2})

    with pytest.raises(SpecError, match="только у своих команд"):
        validate_spec({"title": "x", "steps": [{"id": "a", "tool": "fs.list", "tool_version": 1, "args": {}}]}, INTERNAL)


async def test_an_edit_keeps_the_tasks_version(settings):
    task_id = _task(_spec(1))
    compiler = Compiler(settings, llm=None, main=_Main(V2))

    result = await compiler.edit(task_id, spec=None, ops=[{"op": "replace", "path": "/title", "value": "Строки, иначе"}], summary=None)
    assert store.get_version(task_id, result["version_id"]).spec["steps"][0]["tool_version"] == 1

    # `sep` exists only in v2: the task, still on v1, cannot use it.
    with pytest.raises(ApiError) as e:
        await compiler.edit(task_id, spec=None, ops=[{"op": "add", "path": "/steps/0/args/sep", "value": ";"}], summary=None)
    assert "sep" in str(e.value.details)


async def test_moving_a_task_to_the_current_version(settings):
    task_id = _task(_spec(1))

    result = await Compiler(settings, llm=None, main=_Main(V2)).move_to_tool_version(task_id, "ext.count", None)

    assert store.get_version(task_id, result["version_id"]).spec["steps"][0]["tool_version"] == 2
    assert result["diff"] == ["~ Посчитать «a.txt»: версия команды v1 → v2"]
    assert result["cards"][0]["version"] == 2


async def test_a_version_the_steps_do_not_fit_is_refused(settings):
    task_id = _task(_spec(1))

    with pytest.raises(ApiError) as e:
        await Compiler(settings, llm=None, main=_Main(V3)).move_to_tool_version(task_id, "ext.count", None)

    assert "поправьте задачу словами" in e.value.message
    assert any("path" in error for error in e.value.details["errors"])


needs_uv = pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")


def _two_versions(settings):
    for marker in ("первая", "вторая"):
        code = f"def run(args, ctx):\n    return {{'lines': 0, 'out': '{marker}'}}\n"
        version = tools.save_draft(settings.storage, "count_lines", manifest=MANIFEST, files={"main.py": code}, requirements=[])["version"]
        tools.approve(settings.storage, "count_lines", version)


@needs_uv
async def test_a_step_runs_the_version_it_pins(settings):
    from common.llm import LLM
    from executor.registry import ToolRegistry
    from executor.runtime import ExecutorRuntime

    _two_versions(settings)
    registry = ToolRegistry(settings.storage)
    registry.load()
    runtime = ExecutorRuntime(settings, registry, secrets=None, llm=LLM(settings))

    async def call(version):
        request = ExecuteRequest(tool="ext.count_lines", version=version, args={"file": "a.txt"}, context=ExecContext(mode="run", run_id="run_t"))
        return await runtime.execute(request)

    current, older = await call(None), await call(1)

    assert (current.output["out"], current.version) == ("вторая", 2)
    assert (older.output["out"], older.version) == ("первая", 1)
    assert "нет одобренной версии v7" in (await call(7)).error


@needs_uv
def test_an_old_version_goes_once_no_task_needs_it(settings):
    _two_versions(settings)
    spec = copy.deepcopy(_spec(1))
    spec["steps"][0]["tool"] = "ext.count_lines"
    task_id = _task(spec)

    assert tools.sweep_unused(settings.storage) == 0

    with session() as db:
        db.get(Task, task_id).status = "archived"
        db.commit()

    assert tools.sweep_unused(settings.storage) == 1
    assert not tools.version_dir(settings.storage, "count_lines", 1).exists()
    # The current version stays whoever uses it.
    assert tools.version_dir(settings.storage, "count_lines", 2).exists()
    assert tools.sweep_unused(settings.storage) == 0


def test_the_task_view_tells_of_a_newer_version(settings):
    from common.storage.models import ExternalTool
    from main import views

    task_id = _task(_spec(1))
    assert views.task_detail(task_id)["tool_updates"] == []      # a draft only: no current version yet

    version_id = views.task_detail(task_id)["versions"][0]["id"]
    with session() as db:
        db.get(Task, task_id).current_version_id = version_id
        db.add(ExternalTool(name="count", current_version=2))
        db.commit()

    assert views.task_detail(task_id)["tool_updates"] == [{"tool": "ext.count", "version": 1, "current": 2}]


def test_tasks_can_be_listed_by_the_tool_they_use(settings):
    from main import views

    on_tool = _task(_spec(1))
    _task({"title": "Другое", "steps": [{"id": "files", "tool": "fs.list", "args": {"folder": "."}}]})

    found = views.task_list(tool="ext.count")

    assert [(task["id"], task["tool_version"]) for task in found] == [(on_tool, 1)]
