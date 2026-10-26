"""Executor: validates, checks path arguments, loads declared secrets and runs; what a
dry run means is each tool's own business (the chat asks the user itself)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.schemas.execution import ExecContext, ExecuteRequest
from common.schemas.task import TaskSpec
from common.tools import discover
from executor.runtime import ExecutorRuntime


SPECS = discover()


def test_every_changing_tool_handles_dry_runs_itself():
    """The executor runs every call, dry or not: a tool the chat asks about changes
    something, so it must look at ctx.dry_run itself, or a dry run would really do it."""
    import inspect

    forgot = sorted(
        name for name, spec in SPECS.items()
        if spec.confirm is not False and "dry_run" not in inspect.getsource(spec.fn)
    )

    assert forgot == []


def test_only_the_llm_group_talks_to_the_model():
    """A tool that needs the model gets it as an llm.* step before it: asking the model is
    an explicit step through the executor, never hidden inside another group's tool
    (docs.make_template once did)."""
    import inspect

    asks = {name for name, spec in SPECS.items() if "common.llm" in inspect.getsource(inspect.getmodule(spec.fn)) or "instruct(ctx)" in inspect.getsource(spec.fn)}

    assert asks and all(name.startswith("llm.") for name in asks)
    assert not hasattr(__import__("common.tools", fromlist=["ToolContext"]).ToolContext, "llm")


def test_spec_has_no_approval_section():
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({"title": "x", "approval": {"mode": "never"}, "steps": [{"id": "a", "tool": "fs.list"}]})


class _Registry:
    def get(self, name, version=None):
        return SPECS.get(name)


def _runtime(settings):
    runtime = ExecutorRuntime.__new__(ExecutorRuntime)
    runtime.settings = settings
    runtime.registry = _Registry()
    runtime.secrets = None

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
    response = await _runtime(settings).execute(ExecuteRequest(tool=tool, args=args, context=ExecContext(mode="run")))

    assert response.status == "error" and "Нет доступа" in response.error


async def test_unknown_account_is_refused_before_the_tool_runs(settings):
    request = ExecuteRequest(tool="mail.search", args={"account": "nope"}, context=ExecContext(mode="run"))

    response = await _runtime(settings).execute(request)

    assert response.status == "error" and "nope" in response.error


def test_tools_declare_their_path_and_secret_arguments():
    assert SPECS["fs.move"].path_args == {"src": "path", "dst": "path"}
    assert SPECS["xlsx.aggregate"].path_args == {"inputs": "refs"}
    assert SPECS["llm.extract"].path_args == {}  # text or files: checked when a file is opened
    assert SPECS["mail.send"].secrets == ("account",)


async def test_what_a_tool_said_before_failing_reaches_the_caller(settings, monkeypatch):
    from dataclasses import replace

    from common.tools import ToolError

    async def fails(inp, ctx):
        ctx.log("прочитал 3 файла")
        ctx.notify("начинаю")
        raise ToolError("в четвёртом нет колонки «Сумма»")

    runtime = _runtime(settings)
    monkeypatch.setattr(runtime.registry, "get", lambda name, version=None: replace(SPECS["data.map_rows"], fn=fails))

    response = await runtime.execute(ExecuteRequest(tool="data.map_rows", args={"rows": [], "fields": {}}, context=ExecContext(mode="run")))

    assert response.status == "error"
    assert response.logs == ["прочитал 3 файла"] and response.notifications == ["начинаю"]
