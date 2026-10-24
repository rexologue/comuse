"""External tools: drafts, approval, a run in a process of its own (a real .venv made by
uv; no packages, so no network)."""

from __future__ import annotations

import time

import pytest

from common.external_tools import deps, store
from common.llm import LLM
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

    return ExecutorRuntime(settings, registry, secrets=None, llm=LLM(settings))


def _seen(settings, name="count_lines", version=1) -> str:
    """The hash the person's card shows."""
    return store.version_view(settings.storage, name, version)["hash"]


def _save(settings, name="count_lines", code=COUNT_LINES, **manifest):
    return store.save_draft(settings.storage, name, manifest={**MANIFEST, **manifest}, files={"main.py": code}, requirements=[])


def _approve(settings, runtime, name="count_lines", version=1):
    store.approve(settings.storage, name, version, _seen(settings, name, version))
    runtime.registry.load()


async def _call(runtime, args, tool="ext.count_lines"):
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
    assert _save(settings)["version"] == 1
    assert _save(settings, title="Строки")["version"] == 1

    _approve(settings, runtime)
    second = _save(settings, title="Строки, v2")

    assert second["version"] == 2 and second["status"] == "draft"
    assert [(t["current_version"], t["draft_version"]) for t in store.tool_list()] == [(1, 2)]
    # The chat and new tasks still get v1 until v2 is approved.
    assert runtime.registry.get("ext.count_lines").version == 1


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

    manifest = runtime.registry.get("ext.count_lines").manifest()
    assert manifest["source"] == "external" and manifest["function_name"] == "ext__count_lines"


@needs_uv
async def test_arguments_paths_and_errors(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    assert "Неверные аргументы" in (await _call(runtime, {})).error
    assert "Нет доступа" in (await _call(runtime, {"file": "../outside.txt"})).error
    assert (await _call(runtime, {"file": "missing.txt"})).error == "Нет файла missing.txt"


@needs_uv
async def test_a_crash_and_a_wrong_answer_are_errors(settings, runtime):
    _save(settings, name="crash", code="def run(args, ctx):\n    return 1 / 0\n")
    _save(settings, name="wrong", code="def run(args, ctx):\n    return {'lines': 'many'}\n")
    _approve(settings, runtime, name="crash")
    _approve(settings, runtime, name="wrong")

    crash = await _call(runtime, {"file": "x.txt"}, tool="ext.crash")
    wrong = await _call(runtime, {"file": "x.txt"}, tool="ext.wrong")

    assert crash.error.startswith("ZeroDivisionError") and any("Traceback" in line for line in crash.logs)
    assert "не по его схеме" in wrong.error


@needs_uv
async def test_changed_files_do_not_run(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    (store.version_dir(settings.storage, "count_lines", 1) / "main.py").write_text("def run(args, ctx):\n    return {}\n")

    assert "изменились после одобрения" in (await _call(runtime, {"file": "a.txt"})).error


@needs_uv
async def test_a_tool_and_what_it_started_are_stopped_at_its_timeout(settings, runtime):
    # A child holding the pipes open used to keep the call waiting after the kill.
    code = "import subprocess, sys, time\n\ndef run(args, ctx):\n    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n    time.sleep(30)\n"
    _save(settings, name="sleepy", code=code, timeout=1)
    _approve(settings, runtime, name="sleepy")

    started = time.monotonic()
    response = await _call(runtime, {"file": "a.txt"}, tool="ext.sleepy")

    assert "таймаут" in response.error
    assert time.monotonic() - started < 5


@needs_uv
def test_approval_refuses_a_draft_changed_after_saving(settings):
    _save(settings)
    (store.version_dir(settings.storage, "count_lines", 1) / "main.py").write_text("def run(args, ctx):\n    return {}\n")

    with pytest.raises(ToolError, match="изменились после сохранения"):
        store.approve(settings.storage, "count_lines", 1, _seen(settings))


@needs_uv
def test_a_person_approves_only_the_draft_they_read(settings, runtime):
    _save(settings)
    seen = _seen(settings)
    _save(settings, code=COUNT_LINES + "\n# rewritten by the chat\n")

    with pytest.raises(ToolError, match="изменился, пока вы его читали"):
        store.approve(settings.storage, "count_lines", 1, seen)

    store.approve(settings.storage, "count_lines", 1, _seen(settings))


@needs_uv
def test_a_draft_whose_packages_do_not_resolve_stays_as_it_was(settings, monkeypatch):
    _save(settings)
    before = store.version_view(settings.storage, "count_lines", 1, files=True)

    def unresolvable(folder, cache):
        raise ToolError("Не удалось подобрать пакеты")

    monkeypatch.setattr(store.deps, "lock", unresolvable)
    with pytest.raises(ToolError):
        store.save_draft(settings.storage, "count_lines", manifest=MANIFEST, files={"main.py": "def run(args, ctx):\n    return {}\n"}, requirements=["nothing-like-it"])

    assert store.version_view(settings.storage, "count_lines", 1, files=True) == before
    assert store.files_hash(store.version_dir(settings.storage, "count_lines", 1)) == before["hash"]


@needs_uv
async def test_a_tool_cannot_leave_compiled_code_behind(settings, runtime):
    _save(settings)
    _approve(settings, runtime)

    cache = store.version_dir(settings.storage, "count_lines", 1) / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-314.pyc").write_bytes(b"not what was read")

    assert "изменились после одобрения" in (await _call(runtime, {"file": "a.txt"})).error

