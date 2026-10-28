"""Plugins: drafts, approval by hash, a server per plugin — functions served by our shim
(a real .venv made by uv; no packages, so no network) or a server of the plugin's own."""

from __future__ import annotations

import time

import pytest

from common.schemas.execution import ExecContext, ExecuteRequest
from common.tools import ToolError
from executor.plugins import deps, store
from executor.plugins.launch import Plugins, check_draft
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
    if ctx.dry_run:
        ctx.log("пробный запуск")

    return {"lines": len(lines), "out": str(out), "token": os.environ.get("INTERNAL_TOKEN")}
'''

TOOL = {
    "name": "count_lines",
    "title": "Посчитать строки",
    "description": "Считает строки текстового файла",
    "confirm": False,
    "input_schema": {"type": "object", "properties": {"file": {"type": "string", "x-path": "path"}}, "required": ["file"]},
    "output_schema": {
        "type": "object",
        "properties": {"lines": {"type": "integer"}, "out": {"type": "string"}},
        "required": ["lines", "out"],
    },
}


def _manifest(*tools: dict) -> dict:
    return {"title": "Демо", "tools": list(tools or [TOOL])}


def _save(settings, name="demo", files=None, manifest=None, **kwargs):
    return store.save_draft(settings.storage, name, manifest=manifest or _manifest(), files=files or {"main.py": COUNT_LINES}, requirements=[], **kwargs)


@pytest.fixture
async def plugins(settings):
    registry = ToolRegistry(settings.storage)
    registry.load()
    found = Plugins(registry, settings.storage)

    yield found

    await registry.close()


async def _approve(settings, plugins, name="demo"):
    store.approve(settings.storage, name, store.view(settings.storage, name, "draft")["hash"])
    await plugins.start(name)


@pytest.fixture
def runtime(settings, plugins):
    return ExecutorRuntime(settings, plugins.registry, secrets=None)


async def _call(runtime, args, tool="demo.count_lines", dry_run=False):
    context = ExecContext(mode="run" if dry_run else "chat", session_id="ses_t", run_id="run_t" if dry_run else None, dry_run=dry_run)
    return await runtime.execute(ExecuteRequest(tool=tool, args=args, context=context))


def test_a_draft_names_every_problem(settings):
    with pytest.raises(ToolError) as e:
        store.save_draft(
            settings.storage,
            "Bad-Name",
            manifest={"title": "", "tools": [{**TOOL, "input_schema": {"type": "array"}, "entry": "missing.py"}]},
            files={"helper.py": "def broken(:\n", "../evil.py": "", "plugin.json": "", "__pycache__/main.py": ""},
            requirements=["git+https://example.com/x.git", "dateutil\n-r ../../api_token"],
        )

    message = str(e.value)
    for expected in (
        "«Bad-Name»", "title", "объект", "helper.py, строка 1", "«../evil.py»", "«plugin.json»",
        "git+https", "«__pycache__/main.py»", "«dateutil\n-r ../../api_token»",
    ):
        assert expected in message

    assert not settings.storage.plugins_dir.exists() or not any(settings.storage.plugins_dir.iterdir())


def test_a_plugin_starts_one_way_and_takes_a_group_of_its_own(settings):
    for manifest in ({"title": "Оба", "tools": [TOOL], "command": ["python"]}, {"title": "Ни одного"}):
        with pytest.raises(ToolError, match="одно из двух"):
            _save(settings, manifest=manifest)

    with pytest.raises(ToolError, match="занята встроенными"):
        _save(settings, name="mail", taken_groups={"mail", "fs"})

    with pytest.raises(ToolError, match="нет файла missing.py"):
        _save(settings, manifest=_manifest({**TOOL, "entry": "missing.py"}))

    with pytest.raises(ToolError, match="без пути"):
        _save(settings, manifest={"title": "Чужой", "command": ["/usr/bin/env", "python"]})


@needs_uv
async def test_a_draft_changes_in_place_until_approved(settings, plugins):
    first = _save(settings)
    assert first["status"] == "draft" and [tool["name"] for tool in first["tools"]] == ["demo.count_lines"]

    await _approve(settings, plugins)
    second = _save(settings, files={"main.py": COUNT_LINES + "\n# v2\n"})

    [listed] = store.plugin_list()
    assert listed["current"]["hash"] != listed["draft"]["hash"] == second["hash"]
    # The chat and tasks still get the approved code until the draft is approved.
    assert plugins.registry.manifest("demo.count_lines")["code_hash"] == listed["current"]["hash"]

    await _approve(settings, plugins)
    [listed] = store.plugin_list()
    assert listed["draft"] is None and listed["current"]["hash"] == second["hash"]
    assert plugins.registry.manifest("demo.count_lines")["code_hash"] == second["hash"]
    assert not store.plugin_dir(settings.storage, "demo", "draft").exists()


@needs_uv
async def test_an_approved_plugin_runs_its_functions_in_its_own_process(settings, plugins, runtime, env, monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", "do-not-leak")
    (env / "workspace" / "Входящие").mkdir()
    (env / "workspace" / "Входящие" / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")

    _save(settings)
    await _approve(settings, plugins)

    response = await _call(runtime, {"file": "Входящие/a.txt"})

    assert response.status == "ok", response.error
    assert response.output["lines"] == 3
    # Full paths in the answer come back as refs; the token stayed with the executor.
    assert response.output["out"] == "data:outputs/ses_t/count.txt"
    assert response.output["token"] is None
    assert response.artifacts == ["data:outputs/ses_t/count.txt"]
    assert "строк: 3" in response.logs

    manifest = plugins.registry.manifest("demo.count_lines")
    assert manifest["source"] == "plugin" and manifest["function_name"] == "demo__count_lines" and manifest["confirm"] is False


@needs_uv
async def test_arguments_paths_errors_and_a_dry_run(settings, plugins, runtime, env):
    crash = "def run(args, ctx):\n    return 1 / 0\n"
    wrong = "def run(args, ctx):\n    return {'lines': 'many'}\n"
    tools = [TOOL, {**TOOL, "name": "crash", "entry": "crash.py"}, {**TOOL, "name": "wrong", "entry": "wrong.py"}]
    _save(settings, manifest=_manifest(*tools), files={"main.py": COUNT_LINES, "crash.py": crash, "wrong.py": wrong})
    await _approve(settings, plugins)
    (env / "workspace" / "a.txt").write_text("x\n", encoding="utf-8")

    assert "Неверные аргументы" in (await _call(runtime, {})).error
    assert "Нет доступа" in (await _call(runtime, {"file": "../outside.txt"})).error
    assert (await _call(runtime, {"file": "missing.txt"})).error == "Нет файла missing.txt"

    crashed = await _call(runtime, {"file": "a.txt"}, tool="demo.crash")
    assert crashed.error.startswith("ZeroDivisionError") and any("Traceback" in line for line in crashed.logs)
    assert "не по схеме" in (await _call(runtime, {"file": "a.txt"}, tool="demo.wrong")).error

    # One that never asks is called in a dry run, with ctx.dry_run.
    dry = await _call(runtime, {"file": "a.txt"}, dry_run=True)
    assert dry.status == "ok" and "пробный запуск" in dry.logs


@needs_uv
async def test_changed_files_do_not_start(settings, plugins):
    _save(settings)
    await _approve(settings, plugins)

    (store.plugin_dir(settings.storage, "demo", "current") / "main.py").write_text("def run(args, ctx):\n    return {}\n")
    await plugins.start("demo")

    assert "изменились после одобрения" in plugins.errors["demo"]
    assert plugins.registry.manifest("demo.count_lines") is None


@needs_uv
async def test_a_call_given_up_on_stops_the_process(settings, plugins, runtime, monkeypatch):
    """A function cannot be interrupted in its thread: at the timeout the executor stops
    the plugin's process, and the next call starts it anew."""
    from common import tunables

    slow = "import time\n\ndef run(args, ctx):\n    time.sleep(30)\n    return {'lines': 0, 'out': ''}\n"
    tools = [{**TOOL, "input_schema": {"type": "object"}}, {**TOOL, "name": "slow", "entry": "slow.py", "timeout": 1, "input_schema": {"type": "object"}}]
    fast = "def run(args, ctx):\n    return {'lines': 1, 'out': ''}\n"
    _save(settings, manifest=_manifest(*tools), files={"main.py": fast, "slow.py": slow})
    await _approve(settings, plugins)

    started = time.monotonic()
    response = await _call(runtime, {}, tool="demo.slow")

    assert "таймаут" in response.error and time.monotonic() - started < 5
    assert (await _call(runtime, {})).output["lines"] == 1


def test_approval_takes_only_the_draft_the_person_read(settings):
    _save(settings)
    seen = store.view(settings.storage, "demo", "draft")["hash"]
    _save(settings, files={"main.py": COUNT_LINES + "\n# rewritten by the chat\n"})

    with pytest.raises(ToolError, match="изменился, пока вы его читали"):
        store.approve(settings.storage, "demo", seen)

    (store.plugin_dir(settings.storage, "demo", "draft") / "main.py").write_text("def run(args, ctx):\n    return {}\n")
    with pytest.raises(ToolError, match="изменились после сохранения"):
        store.approve(settings.storage, "demo", store.view(settings.storage, "demo", "draft")["hash"])


@needs_uv
async def test_a_server_of_its_own_is_approved_once_its_tools_were_seen(settings, plugins, runtime, tmp_path):
    """A plugin with a command: its tools are known only when its server runs, and the
    person approves having seen them. (The server here is our shim over another folder.)"""
    from executor.plugins.launch import SHIM

    functions = tmp_path / "functions"
    functions.mkdir()
    (functions / "plugin.json").write_text('{"title": "x", "tools": [{"name": "hello", "title": "Привет", "description": "Здоровается", "entry": "main.py"}]}', encoding="utf-8")
    (functions / "main.py").write_text("def run(args, ctx):\n    return {'text': 'привет'}\n", encoding="utf-8")

    manifest = {"title": "Свой сервер", "command": ["python", "-I", str(SHIM), str(functions)], "confirm_by_tool": {"hello": False}}
    draft = store.save_draft(settings.storage, "srv", manifest=manifest, files={}, requirements=[])
    assert draft["tools"] is None

    with pytest.raises(ToolError, match="Сначала проверьте запуск"):
        store.approve(settings.storage, "srv", draft["hash"])

    checked = await check_draft(settings.storage, "srv")
    assert [(tool["name"], tool["confirm"]) for tool in checked["tools"]] == [("srv.hello", False)]

    await _approve(settings, plugins, "srv")
    assert (await _call(runtime, {}, tool="srv.hello")).output == {"text": "привет"}


def test_a_function_joins_its_plugin_and_a_draft_someone_else_wrote_stays(settings):
    store.save_tool(settings.storage, "demo", "count_lines", entry={k: v for k, v in TOOL.items() if k != "name"}, files={"main.py": COUNT_LINES}, requirements=[])
    view = store.save_tool(
        settings.storage, "demo", "other", entry={**{k: v for k, v in TOOL.items() if k != "name"}, "title": "Другая"},
        files={"main.py": COUNT_LINES, "helper.py": "X = 1\n"}, requirements=[],
    )

    assert [tool["name"] for tool in view["tools"]] == ["demo.count_lines", "demo.other"]
    files = store.view(settings.storage, "demo", "draft", files=True)["files"]
    assert {"count_lines/main.py", "other/main.py", "other/helper.py"} <= set(files)

    with pytest.raises(ToolError, match="написал пользователь"):
        store.save_tool(
            settings.storage, "demo", "third", entry={k: v for k, v in TOOL.items() if k != "name"},
            files={"main.py": COUNT_LINES}, requirements=[], created_by="chat", session_id="ses_x", only_own=True,
        )


def test_a_draft_whose_packages_do_not_resolve_stays_as_it_was(settings, monkeypatch):
    _save(settings)
    before = store.view(settings.storage, "demo", "draft", files=True)

    def unresolvable(folder, cache):
        raise ToolError("Не удалось подобрать пакеты")

    monkeypatch.setattr(deps, "lock", unresolvable)
    with pytest.raises(ToolError):
        store.save_draft(settings.storage, "demo", manifest=_manifest(), files={"main.py": "def run(args, ctx):\n    return {}\n"}, requirements=["nothing-like-it"])

    assert store.view(settings.storage, "demo", "draft", files=True) == before


TOUCH = """
from pathlib import Path


def run(args, ctx):
    if ctx.dry_run:
        ctx.log("Пробный запуск: дописал бы строку")
        return {"lines": 0, "out": ""}

    path = Path(args["file"])
    path.write_text(path.read_text(encoding="utf-8") + "ещё\\n", encoding="utf-8")
    return {"lines": 1, "out": str(path)}
"""


async def test_a_dry_run_calls_a_changing_function_only_if_it_says_it_does_one(settings, plugins, runtime, env):
    """Code a chat wrote may not look at ctx.dry_run: unless its manifest says it does, a
    dry run does not call a function that changes something."""
    changing = {**TOOL, "confirm": True}
    tools = [{**changing, "name": "careless", "entry": "touch.py"}, {**changing, "name": "careful", "entry": "touch.py", "dry_run": True}]
    _save(settings, manifest=_manifest(*tools), files={"main.py": COUNT_LINES, "touch.py": TOUCH})
    await _approve(settings, plugins)
    target = env / "workspace" / "a.txt"
    target.write_text("x\\n", encoding="utf-8")

    careless = await _call(runtime, {"file": "a.txt"}, tool="demo.careless", dry_run=True)
    careful = await _call(runtime, {"file": "a.txt"}, tool="demo.careful", dry_run=True)

    assert careless.status == "ok" and any("не вызван" in line for line in careless.logs)
    assert careful.status == "ok" and "Пробный запуск: дописал бы строку" in careful.logs
    assert target.read_text(encoding="utf-8") == "x\\n"
