"""Editing a task without the LLM: a whole spec or a JSON Patch becomes a draft version."""

from __future__ import annotations

import pytest

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.tools import discover
from compiler import store
from compiler.compile import Compiler


CATALOG = {name: spec.manifest() for name, spec in discover().items()}

SPEC = {
    "title": "Сводка",
    "trigger": {"type": "cron", "expr": "0 9 * * MON"},
    "steps": [{"id": "files", "tool": "fs.list", "args": {"folder": "Отчёты", "pattern": "*.xlsx"}}],
}


class _Main:
    async def get(self, path, **_):
        assert path == "/tools"
        return {"tools": list(CATALOG.values())}


def _compiler(settings) -> Compiler:
    return Compiler(settings, llm=None, main=_Main())


def _task() -> str:
    version = store.create_task(TaskSpec.model_validate(SPEC), session_id="ses_1", cards=[], summary="", settings={})
    return version.task_id


async def test_json_patch_edit_makes_a_draft_with_a_diff(settings):
    task_id = _task()

    result = await _compiler(settings).edit(
        task_id, spec=None, ops=[{"op": "replace", "path": "/trigger/expr", "value": "0 9 * * FRI"}], summary=None
    )

    version = store.get_version(task_id, result["version_id"])
    assert version.status == "draft" and version.created_by == "manual" and version.session_id is None
    assert any("пятницам" in line for line in result["diff"])


async def test_whole_spec_edit(settings):
    task_id = _task()
    changed = {**SPEC, "steps": [{"id": "files", "tool": "fs.list", "args": {"folder": "Архив"}}]}

    result = await _compiler(settings).edit(task_id, spec=changed, ops=None, summary="Другая папка")

    assert result["summary"] == "Другая папка"
    assert store.get_version(task_id, result["version_id"]).spec["steps"][0]["args"]["folder"] == "Архив"


@pytest.mark.parametrize(
    ("spec", "ops"),
    [
        (None, None),                                                         # nothing to apply
        (SPEC, [{"op": "remove", "path": "/title"}]),                         # both at once
        (None, [{"op": "replace", "path": "/steps/0/tool", "value": "fs.nope"}]),  # fails the checks
        (None, [{"op": "remove", "path": "/nowhere"}]),                       # patch does not apply
        (None, [{"op": "replace", "path": "/title", "value": "Сводка"}]),     # changes nothing
    ],
)
async def test_bad_edits_are_refused(settings, spec, ops):
    task_id = _task()

    with pytest.raises(ApiError):
        await _compiler(settings).edit(task_id, spec=spec, ops=ops, summary=None)


async def test_agent_dry_run_reports_the_run_as_is():
    from agent_loop import meta_tools

    class Main:
        async def post(self, path, json=None):
            assert path == "/runs" and json == {"task_version_id": "ver_1", "dry_run": True, "wait": True}
            return {"id": "run_1", "status": "failed", "error": "Шаг `files`: нет папки",
                    "steps": [{"step_key": "files", "status": "error", "error": "нет папки"}]}

    result = await meta_tools.call(Main(), "ses_1", "task_dry_run", {"task_id": "task_1", "version_id": "ver_1"})

    assert result["output"]["status"] == "failed"
    assert result["output"]["steps"] == [{"step": "files", "status": "error", "error": "нет папки", "output": None}]


async def test_a_second_change_keeps_the_first_one(settings):
    """After approval, changes asked one after another build on each other, not each on
    the approved version: approving the last draft must not lose the earlier change."""
    task_id = _task()
    first = store.base_version(task_id)
    store.approve(task_id, first.id, CATALOG)
    compiler = _compiler(settings)

    cc = await compiler.edit(task_id, spec=None, ops=[{"op": "replace", "path": "/steps/0/args/folder", "value": "Архив"}], summary=None)
    day = await compiler.edit(task_id, spec=None, ops=[{"op": "replace", "path": "/trigger/expr", "value": "0 9 * * FRI"}], summary=None)

    latest = store.get_version(task_id, day["version_id"])
    assert latest.spec["steps"][0]["args"]["folder"] == "Архив"
    assert latest.spec["trigger"]["expr"] == "0 9 * * FRI"
    assert latest.parent_version_id == cc["version_id"]
    assert store.get_version(task_id, cc["version_id"]).status == "superseded"


async def test_a_new_draft_voids_the_older_drafts_approval(settings):
    from main.events import EventBus
    from main.orchestrator import Orchestrator

    class Compiler:
        def __init__(self):
            self.no = 0

        async def post(self, path, json=None, **_):
            self.no += 1
            return {"task_id": "task_1", "version_id": f"ver_{self.no}"}

    class Registry:
        def __init__(self):
            self.compiler = Compiler()

        def client(self, name):
            return self.compiler

    orchestrator = Orchestrator(Registry(), EventBus())

    first = await orchestrator.edit("task_1", {"ops": []})
    second = await orchestrator.edit("task_1", {"ops": []})

    assert orchestrator._get(first["approval_id"])["status"] == "cancelled"
    assert orchestrator._get(second["approval_id"])["status"] == "pending"


async def test_archiving_a_task_voids_its_drafts_approval(settings):
    from main.events import EventBus
    from main.orchestrator import Orchestrator

    class Client:
        async def post(self, path, json=None, **_):
            return {"task_id": "task_1", "version_id": "ver_1", "status": "archived"}

    class Registry:
        def client(self, name):
            return Client()

    orchestrator = Orchestrator(Registry(), EventBus())
    draft = await orchestrator.edit("task_1", {"ops": []})

    await orchestrator.set_task_status("task_1", "archive")

    assert orchestrator._get(draft["approval_id"])["status"] == "cancelled"


async def test_every_change_of_a_step_shows_in_the_diff(settings):
    task_id = _task()
    compiler = _compiler(settings)

    result = await compiler.edit(
        task_id, spec=None, summary=None,
        ops=[{"op": "add", "path": "/steps/0/on_error", "value": "retry"}, {"op": "add", "path": "/steps/0/retries", "value": 2}],
    )
    assert any("при ошибке: fail → retry" in line and "повторов: 0 → 2" in line for line in result["diff"])

    result = await compiler.edit(task_id, spec=None, summary=None, ops=[{"op": "add", "path": "/variables/boss", "value": "a@company.local"}])
    assert any("переменные" in line and "boss" in line for line in result["diff"])


async def test_an_edit_made_meanwhile_is_not_superseded(settings):
    task_id = _task()
    base = store.base_version(task_id)

    # Someone saved an edit while this one was being prepared on `base`.
    await _compiler(settings).edit(task_id, spec=None, summary=None, ops=[{"op": "replace", "path": "/title", "value": "Другая"}])

    with pytest.raises(ApiError) as e:
        store.add_version(task_id, TaskSpec.model_validate(SPEC), parent=base, patch=None, cards=[], summary="", created_by="llm", session_id=None)
    assert e.value.status_code == 409


def test_a_broken_card_or_schema_of_an_own_command_breaks_nothing():
    from compiler.cards import build_cards
    from compiler.prompts import format_catalog
    from compiler.validate import validate_spec

    manifest = {
        **CATALOG["fs.list"], "name": "ext.odd", "title": "Странная", "card": "Сумма {amount:.2f} {0} {",
        "input_schema": {"type": "object", "properties": {"amount": True}}, "version": 1,
    }
    catalog = {**CATALOG, "ext.odd": manifest}
    spec = validate_spec({"title": "x", "steps": [{"id": "a", "tool": "ext.odd", "tool_version": 1, "args": {"amount": 5}}]}, catalog)

    assert build_cards(spec, catalog)[0]["text"] == "Странная"
    assert "ext.odd" in format_catalog(catalog)


async def test_chat_only_tools_are_not_offered_to_tasks(settings):
    catalog = await _compiler(settings).catalog()

    assert "tools.draft" not in catalog and "fs.list" in catalog
