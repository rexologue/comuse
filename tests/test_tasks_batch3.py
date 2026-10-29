"""What tasks and the compiler got wrong in the stress test: a foreach result that cannot
say what each item was, references into it that pass and fail at the run, a user's own
values turned into variables nobody fills, a dead task after its only draft goes, a tried
command left out, a diff against a draft in between, a gone command with no way out, run
artifacts that are not where the files are."""

from __future__ import annotations

import pytest

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.storage.db import session
from common.storage.models import Task
from common.tools import discover
from common.tools.definition import snapshot
from compiler import store
from compiler.compile import Compiler
from compiler.validate import SpecError, validate_spec
from runner.engine import RunEngine
from tests.conftest import FakeMain
from tests.test_engine import SPEC as SORT, _make_run, _tools


CATALOG = {name: spec.manifest() for name, spec in discover().items()}


class _Main:
    async def tools(self):
        return CATALOG


def _compiler(settings) -> Compiler:
    return Compiler(settings, llm=None, main=_Main())


async def test_a_foreach_result_holds_each_item_beside_its_steps(settings):
    main = FakeMain(_tools())
    spec = {**SORT, "steps": [*SORT["steps"], {"id": "tell", "tool": "user.notify", "args": {
        "message": "{% for r in sort.output %}{{ r.file.path }} → {{ r.move.path }}; {% endfor %}"}}]}
    main.handlers["user.notify"] = lambda args, ctx: {"output": {"sent": True}}

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["status"] == "succeeded"
    told = next(c for c in main.calls if c["tool"] == "user.notify")["args"]["message"]
    assert told == "Входящие/a.txt → Документы/счёт/; Входящие/b.txt → Документы/акт/; "


@pytest.mark.parametrize(
    ("message", "says"),
    [
        ("{% for r in sort %}{{ r.move.path }}{% endfor %}", "результат — `sort.output`"),
        ("{{ sort.output | map(attribute='move.output.path') | join(', ') }}", "лежит прямо под его id"),
        ("{% for r in sort.output %}{{ r.kind.output.label }}{% endfor %}", "лежит прямо под его id"),
    ],
)
def test_wrong_references_into_a_foreach_result_are_caught(message, says):
    spec = {**SORT, "steps": [*SORT["steps"], {"id": "tell", "tool": "user.notify", "args": {"message": message}}]}

    with pytest.raises(SpecError) as e:
        validate_spec(spec, CATALOG)

    assert any(says in error for error in e.value.errors)


def test_the_right_reference_into_a_foreach_result_passes():
    spec = {**SORT, "steps": [*SORT["steps"], {"id": "tell", "tool": "user.notify", "args": {
        "message": "{% for r in sort.output %}{{ r.file.path }}: {{ r.kind.label }}{% endfor %}"}}]}

    assert validate_spec(spec, CATALOG).steps[-1].id == "tell"


def test_a_variable_nobody_fills_is_refused():
    spec = {"title": "Акт", "variables": {"client": ""}, "steps": [{"id": "tell", "tool": "user.notify", "args": {"message": "{{ client }}"}}]}

    with pytest.raises(SpecError, match="у задачи нет ввода при запуске"):
        validate_spec(spec, CATALOG)


def _task(spec=None) -> str:
    checked = TaskSpec.model_validate(spec or {"title": "Сводка", "steps": [{"id": "files", "tool": "fs.list", "args": {"folder": "Отчёты"}}]})
    return store.create_task(checked, session_id="ses_1", cards=[], summary="", settings={}, tools=snapshot(checked.tools(), CATALOG)).task_id


def test_a_task_whose_only_draft_is_rejected_goes_to_the_archive(settings):
    task_id = _task()

    store.reject(task_id, store.base_version(task_id).id)

    with session() as db:
        assert db.get(Task, task_id).status == "archived"


async def test_rejecting_an_edit_of_a_draft_gives_the_draft_back(settings):
    """Not the archive for a task never approved: what is rejected is the edit."""
    task_id = _task()
    first = store.base_version(task_id).id

    edited = await _compiler(settings).edit(task_id, spec=None, ops=[{"op": "replace", "path": "/steps/0/args/folder", "value": "Архив"}], summary=None)
    store.reject(task_id, edited["version_id"])

    with session() as db:
        assert db.get(Task, task_id).status == "draft"
    assert store.base_version(task_id).id == first


async def test_a_draft_shows_what_changes_against_what_runs_now(settings):
    """Two changes after approval: the second draft's diff has both, as approving it does."""
    task_id = _task()
    store.approve(task_id, store.base_version(task_id).id, CATALOG)
    compiler = _compiler(settings)

    await compiler.edit(task_id, spec=None, ops=[{"op": "replace", "path": "/steps/0/args/folder", "value": "Архив"}], summary=None)
    second = await compiler.edit(task_id, spec=None, ops=[{"op": "replace", "path": "/trigger", "value": {"type": "cron", "expr": "0 9 * * FRI"}}], summary=None)

    shown = "\n".join(second["diff"])
    assert "Архив" in shown and "по пятницам" in shown
    assert (await compiler.diff(task_id, second["version_id"]))["lines"] == second["diff"]


async def test_a_gone_command_says_what_can_be_done(settings):
    task_id = _task()
    store.approve(task_id, store.base_version(task_id).id, CATALOG)
    catalog = {name: manifest for name, manifest in CATALOG.items() if name != "fs.list"}

    class Gone:
        async def tools(self):
            return catalog

    with pytest.raises(ApiError, match="больше нет: обновить нечем"):
        await Compiler(settings, llm=None, main=Gone()).refresh_tools(task_id)


def test_a_command_tried_in_the_chat_counts_as_done(settings):
    from agent_loop import history

    session_id = history.ensure_session(None, "Попробуй")
    message_id = history.add_message(session_id, "assistant", "")
    history.add_trace(session_id, message_id, "tools.try", {"name": "st_text.upper", "args": {}}, {"status": "ok", "output": {}}, "confirmed")

    assert "st_text.upper" in history.transcript(session_id)["done_tools"]


async def test_artifacts_are_where_the_files_are(settings):
    main = FakeMain({
        "xlsx.write_table": lambda args, ctx: {"output": {"path": "data:outputs/run_1/Сводка.xlsx"}, "artifacts": ["data:outputs/run_1/Сводка.xlsx"]},
        "fs.move": lambda args, ctx: {"output": {"path": "Отчёты/Сводка.xlsx"}, "moved": {"data:outputs/run_1/Сводка.xlsx": "Отчёты/Сводка.xlsx"}},
    })
    spec = {"title": "Сводка", "steps": [
        {"id": "table", "tool": "xlsx.write_table", "args": {"rows": [], "output_name": "Сводка"}},
        {"id": "move", "tool": "fs.move", "args": {"src": "{{ table.output.path }}", "dst": "Отчёты/"}},
    ]}

    run = await RunEngine(main).execute(_make_run(spec))

    assert run["result"]["artifacts"] == ["Отчёты/Сводка.xlsx"]
