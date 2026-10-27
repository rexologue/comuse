"""External tools: drafts, approval, a run in a process of its own (a real .venv made by
uv; no packages, so no network)."""

from __future__ import annotations

import time

import pytest

from common.external_tools import deps, store
from common.schemas.execution import ExecContext, ExecuteRequest
from common.tools import ToolError
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


try:
    deps.uv_bin()
    HAVE_UV = True
except ToolError:
    HAVE_UV = False

needs_uv = pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")


COUNT_LINES = '''
import os
from pathlib import Path

from agent_tool import ToolError


def run(args, ctx):
    path = Path(args["file"])
    if not path.is_file():
        raise ToolError(f"Нет файла {path.name}")

    print("считаю строки")
    lines = path.read_text(encoding="utf-8").splitlines()
    out = ctx.output_path("count.txt")
    out.write_text(str(len(lines)), encoding="utf-8")
    ctx.log(f"строк: {len(lines)}")

    return {"lines": len(lines), "out": str(out), "token": os.environ.get("INTERNAL_TOKEN")}
'''

MANIFEST = {
    "title": "Посчитать строки",
    "description": "Считает строки текстового файла",
    "input_schema": {
        "type": "object",
        "properties": {"file": {"type": "string", "x-path": "path"}},
        "required": ["file"],
    },
    "output_schema": {
        "type": "object",
        "properties": {"lines": {"type": "integer"}, "out": {"type": "string"}},
        "required": ["lines", "out"],
    },
}


@pytest.fixture
def runtime(settings):
    registry = ToolRegistry(settings.storage)
    registry.load()

    return ExecutorRuntime(settings, registry, secrets=None)


def _seen(settings, name="demo.count_lines") -> str:
    """The hash the person's card shows."""
    return store.view(settings.storage, name, "draft")["hash"]


def _save(settings, name="demo.count_lines", code=COUNT_LINES, **manifest):
    return store.save_draft(settings.storage, name, manifest={**MANIFEST, **manifest}, files={"main.py": code}, requirements=[])


def _approve(settings, runtime, name="demo.count_lines"):
    store.approve(settings.storage, name, _seen(settings, name))
    runtime.registry.load()


async def _call(runtime, args, tool="demo.count_lines"):
    return await runtime.execute(ExecuteRequest(tool=tool, args=args, context=ExecContext(mode="chat", session_id="ses_t")))


def test_a_draft_names_every_problem(settings):
    with pytest.raises(ToolError) as e:
        store.save_draft(
            settings.storage,
            "Bad-Name",
            manifest={"title": "", "description": "x", "input_schema": {"type": "array"}},
            files={"helper.py": "def broken(:\n", "../evil.py": "", "tool.json": "", "__pycache__/main.py": ""},
            requirements=["git+https://example.com/x.git", "dateutil\n-r ../../api_token"],
        )

    message = str(e.value)
    for expected in (
        "«Bad-Name»", "title", "объект", "Нет main.py", "helper.py, строка 1", "«../evil.py»", "«tool.json»", "git+https",
        "«__pycache__/main.py»", "«dateutil\n-r ../../api_token»",
    ):
        assert expected in message

    assert not settings.storage.external_tools_dir.exists() or not any(settings.storage.external_tools_dir.iterdir())


@needs_uv
def test_a_draft_changes_in_place_until_approved(settings, runtime):
    _save(settings)
    assert _save(settings, title="Строки")["status"] == "draft"
    assert len(store.tool_list()) == 1 and store.tool_list()[0]["current"] is None

    _approve(settings, runtime)
    second = _save(settings, code=COUNT_LINES + "\n# v2\n")

    assert second["status"] == "draft"
    [listed] = store.tool_list()
    assert listed["current"]["hash"] != listed["draft"]["hash"]
    # The chat and tasks still get the approved code until the draft is approved.
    code_hash = runtime.registry.manifest("demo.count_lines")["code_hash"]
    assert code_hash == listed["current"]["hash"]

    _approve(settings, runtime)
    [listed] = store.tool_list()
    assert listed["draft"] is None and listed["current"]["hash"] == second["hash"]
    assert runtime.registry.manifest("demo.count_lines")["code_hash"] == second["hash"]
    assert not store.tool_dir(settings.storage, "demo.count_lines", "draft").exists()


@needs_uv
async def test_an_approved_version_runs_in_its_own_process(settings, runtime, env, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "do-not-leak")
    (env / "workspace" / "Входящие").mkdir()
    (env / "workspace" / "Входящие" / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")

    _save(settings)
    _approve(settings, runtime)

    response = await _call(runtime, {"file": "Входящие/a.txt"})

    assert response.status == "ok", response.error
    assert response.output["lines"] == 3
    # Full paths in the answer come back as refs; the token stayed with the executor.
    assert response.output["out"] == "data:outputs/ses_t/count.txt"
    assert response.output["token"] is None
    assert response.artifacts == ["data:outputs/ses_t/count.txt"]
    assert "строк: 3" in response.logs and any("считаю строки" in line for line in response.logs)

    manifest = runtime.registry.get("demo.count_lines").manifest()
    assert manifest["source"] == "external" and manifest["function_name"] == "demo__count_lines"


@needs_uv
async def test_arguments_paths_and_errors(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    assert "Неверные аргументы" in (await _call(runtime, {})).error
    assert "Нет доступа" in (await _call(runtime, {"file": "../outside.txt"})).error
    assert (await _call(runtime, {"file": "missing.txt"})).error == "Нет файла missing.txt"


@needs_uv
async def test_a_crash_and_a_wrong_answer_are_errors(settings, runtime):
    _save(settings, name="demo.crash", code="def run(args, ctx):\n    return 1 / 0\n")
    _save(settings, name="demo.wrong", code="def run(args, ctx):\n    return {'lines': 'many'}\n")
    _approve(settings, runtime, name="demo.crash")
    _approve(settings, runtime, name="demo.wrong")

    crash = await _call(runtime, {"file": "x.txt"}, tool="demo.crash")
    wrong = await _call(runtime, {"file": "x.txt"}, tool="demo.wrong")

    assert crash.error.startswith("ZeroDivisionError") and any("Traceback" in line for line in crash.logs)
    assert "не по его схеме" in wrong.error


@needs_uv
async def test_changed_files_do_not_run(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    (store.tool_dir(settings.storage, "demo.count_lines", "current") / "main.py").write_text("def run(args, ctx):\n    return {}\n")

    assert "изменились после одобрения" in (await _call(runtime, {"file": "a.txt"})).error


@needs_uv
async def test_a_tool_and_what_it_started_are_stopped_at_its_timeout(settings, runtime):
    # A child holding the pipes open used to keep the call waiting after the kill.
    code = "import subprocess, sys, time\n\ndef run(args, ctx):\n    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n    time.sleep(30)\n"
    _save(settings, name="demo.sleepy", code=code, timeout=1)
    _approve(settings, runtime, name="demo.sleepy")

    started = time.monotonic()
    response = await _call(runtime, {"file": "a.txt"}, tool="demo.sleepy")

    assert "таймаут" in response.error
    assert time.monotonic() - started < 5


@needs_uv
def test_approval_refuses_a_draft_changed_after_saving(settings):
    _save(settings)
    (store.tool_dir(settings.storage, "demo.count_lines", "draft") / "main.py").write_text("def run(args, ctx):\n    return {}\n")

    with pytest.raises(ToolError, match="изменились после сохранения"):
        store.approve(settings.storage, "demo.count_lines", _seen(settings))


@needs_uv
def test_a_person_approves_only_the_draft_they_read(settings, runtime):
    _save(settings)
    seen = _seen(settings)
    _save(settings, code=COUNT_LINES + "\n# rewritten by the chat\n")

    with pytest.raises(ToolError, match="изменился, пока вы его читали"):
        store.approve(settings.storage, "demo.count_lines", seen)

    store.approve(settings.storage, "demo.count_lines", _seen(settings))


@needs_uv
def test_a_draft_whose_packages_do_not_resolve_stays_as_it_was(settings, monkeypatch):
    _save(settings)
    before = store.view(settings.storage, "demo.count_lines", "draft", files=True)

    def unresolvable(folder, cache):
        raise ToolError("Не удалось подобрать пакеты")

    monkeypatch.setattr(store.deps, "lock", unresolvable)
    with pytest.raises(ToolError):
        store.save_draft(settings.storage, "demo.count_lines", manifest=MANIFEST, files={"main.py": "def run(args, ctx):\n    return {}\n"}, requirements=["nothing-like-it"])

    assert store.view(settings.storage, "demo.count_lines", "draft", files=True) == before
    assert store.files_hash(store.tool_dir(settings.storage, "demo.count_lines", "draft")) == before["hash"]


@needs_uv
async def test_a_tool_cannot_leave_compiled_code_behind(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    cache = store.tool_dir(settings.storage, "demo.count_lines", "current") / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-314.pyc").write_bytes(b"not what was read")

    assert "изменились после одобрения" in (await _call(runtime, {"file": "a.txt"})).error



def test_an_own_command_takes_a_group_of_its_own(settings):
    """group.action like a built-in command, but never in a built-in group."""
    for name, problem in (("count_lines", "группа.действие"), ("mail.forward", "группа «mail» встроенная")):
        with pytest.raises(ToolError, match=problem):
            store.save_draft(settings.storage, name, manifest=MANIFEST, files={"main.py": COUNT_LINES}, requirements=[], builtin_groups={"mail", "fs"})

    view = store.save_draft(settings.storage, "telegram.send", manifest=MANIFEST, files={"main.py": COUNT_LINES}, requirements=[], builtin_groups={"mail"})
    assert view["name"] == "telegram.send"
    assert store.tool_dir(settings.storage, "telegram.send", "draft") == settings.storage.external_tools_dir / "telegram" / "send" / "draft"
