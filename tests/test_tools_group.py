"""tools.*: the chat writes an own command into the draft of its plugin, tries it, and
leaves the approval to the user."""

from __future__ import annotations

import pytest

from common import tunables
from common.tools import ToolContext, ToolError
from executor.paths import WorkspacePaths
from executor.plugins import store
from internal_tools.tools import draft as draft_tool
from internal_tools.tools import list as list_tool
from internal_tools.tools import show as show_tool
from internal_tools.tools import trial as trial_tool
from tests.test_plugins import COUNT_LINES, HAVE_UV, TOOL


def _ctx(settings, mode="chat", dry_run=False) -> ToolContext:
    paths = WorkspacePaths(tunables.get("workspace_dir"), settings.storage.data_dir)
    return ToolContext(mode=mode, settings=settings, paths=paths, session_id="ses_tools", dry_run=dry_run)


def _draft_input(**changes) -> draft_tool.Input:
    fields = {
        "name": "demo.count_lines",
        "title": TOOL["title"],
        "description": TOOL["description"],
        "input_schema": TOOL["input_schema"],
        "output_schema": TOOL["output_schema"],
        "files": {"main.py": COUNT_LINES},
    }

    return draft_tool.Input(**{**fields, **changes})


@pytest.mark.parametrize(("mode", "dry_run"), [("run", False), ("chat", True)])
async def test_only_a_chat_writes_and_tries_commands(settings, mode, dry_run):
    with pytest.raises(ToolError, match="только в чате"):
        await draft_tool.draft(_draft_input(), _ctx(settings, mode, dry_run))

    with pytest.raises(ToolError, match="только в чате"):
        await trial_tool.trial(trial_tool.Input(name="demo.count_lines"), _ctx(settings, mode, dry_run))


async def test_a_bad_draft_names_its_problems(settings):
    with pytest.raises(ToolError) as e:
        await draft_tool.draft(_draft_input(files={"main.py": "def run(args, ctx)\n"}), _ctx(settings))

    assert "main.py, строка 1" in str(e.value)


@pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")
async def test_the_chat_drafts_tries_and_shows_a_command(settings, env):
    (env / "workspace" / "a.txt").write_text("один\nдва\n", encoding="utf-8")
    ctx = _ctx(settings)

    written = await draft_tool.draft(_draft_input(), ctx)
    assert (written.tool, written.plugin, written.tools, written.status) == ("demo.count_lines", "demo", ["demo.count_lines"], "draft")

    tried = await trial_tool.trial(trial_tool.Input(name="demo.count_lines", args={"file": "a.txt"}), ctx)
    assert tried.output["lines"] == 2 and tried.output["out"] == "data:outputs/ses_tools/count.txt"

    shown = await show_tool.show(show_tool.Input(name="demo.count_lines"), ctx)
    assert shown.status == "draft" and shown.files["count_lines/main.py"] == COUNT_LINES

    listed = await list_tool.list_tools(list_tool.Input(), ctx)
    assert listed.plugins[0]["draft"] is not None and listed.plugins[0]["current"] is None
    # The card in the chat finds the draft by the chat that wrote it.
    assert listed.plugins[0]["draft"]["session_id"] == "ses_tools"
    assert listed.plugins[0]["draft"]["created_by"] == "chat"


@pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")
async def test_a_draft_changed_behind_its_back_does_not_run(settings):
    ctx = _ctx(settings)
    await draft_tool.draft(_draft_input(), ctx)

    (store.plugin_dir(settings.storage, "demo", "draft") / "count_lines" / "main.py").write_text("import os\n")

    with pytest.raises(ToolError, match="изменились после сохранения"):
        await trial_tool.trial(trial_tool.Input(name="demo.count_lines", args={"file": "a.txt"}), ctx)


async def test_the_chat_does_not_replace_someone_elses_draft(settings):
    store.save_draft(settings.storage, "demo", manifest={"title": "Демо", "tools": [TOOL]}, files={"main.py": COUNT_LINES}, requirements=[])

    with pytest.raises(ToolError, match="написал пользователь"):
        await draft_tool.draft(_draft_input(), _ctx(settings))

    assert store.view(settings.storage, "demo", "draft")["created_by"] == "user"



def test_the_chat_asks_before_it_writes_code_and_the_card_says_where():
    from common.tools import discover

    manifest = discover()["tools.draft"].manifest()

    assert manifest["confirm"] is True and "черновик её плагина" in manifest["card"]
    assert "dry_run" in manifest["input_schema"]["properties"]


async def test_a_file_is_replaced_only_when_asked(settings, env):
    from internal_tools.fs.write import Input, write_file

    paths = WorkspacePaths(env / "workspace", settings.storage.data_dir)
    ctx = ToolContext(mode="chat", settings=settings, paths=paths, session_id="ses_w")
    (env / "workspace" / "note.txt").write_text("старое", encoding="utf-8")

    with pytest.raises(ToolError, match="уже существует: заменить его — overwrite=true"):
        await write_file(Input(path="note.txt", content="новое"), ctx)
    assert (env / "workspace" / "note.txt").read_text(encoding="utf-8") == "старое"

    await write_file(Input(path="note.txt", content="новое", overwrite=True), ctx)
    assert (env / "workspace" / "note.txt").read_text(encoding="utf-8") == "новое"
