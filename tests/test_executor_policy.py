"""Executor side-effect policy: chat asks first, dry runs suppress effects, task runs
never wait (each tool decides by its domain settings)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.schemas.execution import ExecContext, ExecuteRequest
from common.schemas.task import TaskSpec
from common.tools import discover
from executor.runtime import ExecutorRuntime


SPECS = discover()


def _decide(tool: str, **ctx):
    runtime = ExecutorRuntime.__new__(ExecutorRuntime)
    return runtime._decide(SPECS[tool], ExecContext(**ctx))


def test_reads_never_ask():
    decision = _decide("fs.list", mode="chat")
    assert not decision.ask_first and not decision.skip


def test_chat_send_asks_first():
    decision = _decide("mail.send", mode="chat")
    assert decision.ask_first


def test_confirmed_send_goes_through():
    decision = _decide("mail.send", mode="chat", confirmed=True)
    assert not decision.ask_first and not decision.draft


def test_dry_run_send_is_a_draft_and_move_is_skipped():
    assert _decide("mail.send", mode="run", dry_run=True).draft
    assert _decide("fs.move", mode="run", dry_run=True).skip


def test_task_run_never_waits():
    decision = _decide("mail.send", mode="run")
    assert not decision.ask_first and not decision.draft and not decision.skip


def test_spec_has_no_approval_section():
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({"title": "x", "approval": {"mode": "never"}, "steps": [{"id": "a", "tool": "fs.list"}]})


class _Registry:
    def get(self, name):
        return SPECS.get(name)


def _runtime(settings):
    from executor.paths import WorkspacePaths

    runtime = ExecutorRuntime.__new__(ExecutorRuntime)
    runtime.settings = settings
    runtime.registry = _Registry()
    runtime.paths = WorkspacePaths(settings.storage.workspace_dir, settings.storage.data_dir)
    runtime.secrets = None
    runtime.llm = None
    runtime.main = None

    return runtime


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("fs.list", {"folder": "../../etc"}),
        ("docs.read", {"path": "/etc/passwd"}),
        ("xlsx.aggregate", {"inputs": ["Отчёты/a.xlsx", "data:app.db"]}),
        ("mail.send", {"account": "w", "to": ["a@b.ru"], "subject": "s", "body": "b", "attachments": ["../secret.txt"]}),
    ],
)
async def test_path_arguments_are_checked_before_the_tool_runs(settings, tool, args):
    response = await _runtime(settings)._execute(ExecuteRequest(tool=tool, args=args, context=ExecContext(mode="run")))

    assert response.status == "error" and "Нет доступа" in response.error


async def test_unknown_account_is_refused_before_the_tool_runs(settings):
    request = ExecuteRequest(tool="mail.search", args={"account": "nope"}, context=ExecContext(mode="run"))

    response = await _runtime(settings)._execute(request)

    assert response.status == "error" and "nope" in response.error


def test_tools_declare_their_path_and_secret_arguments():
    assert SPECS["fs.move"].path_args == {"src": "path", "dst": "path"}
    assert SPECS["xlsx.aggregate"].path_args == {"inputs": "refs"}
    assert SPECS["llm.extract"].path_args == {}  # text or files: checked when a file is opened
    assert SPECS["mail.send"].secrets == ("account",)
