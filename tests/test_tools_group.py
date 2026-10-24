"""tools.*: the chat writes a draft of an own command, tries it, and leaves the approval
to the user."""

from __future__ import annotations

import pytest

from common import tunables
from common.external_tools import store
from common.tools import ToolContext, ToolError
from executor.paths import WorkspacePaths
from internal_tools.tools import draft as draft_tool
from internal_tools.tools import list as list_tool
from internal_tools.tools import show as show_tool
from internal_tools.tools import trial as trial_tool
from tests.test_external_tools import COUNT_LINES, HAVE_UV, MANIFEST


def _ctx(settings, mode="chat", dry_run=False) -> ToolContext:
    paths = WorkspacePaths(tunables.get("workspace_dir"), settings.storage.data_dir)
    return ToolContext(mode=mode, settings=settings, paths=paths, llm=None, session_id="ses_tools", dry_run=dry_run)


def _draft_input(**changes) -> draft_tool.Input:
    fields = {
        "name": "ext.count_lines",
        "title": MANIFEST["title"],
        "description": MANIFEST["description"],
        "input_schema": MANIFEST["input_schema"],
        "output_schema": MANIFEST["output_schema"],
        "files": {"main.py": COUNT_LINES},
    }

    return draft_tool.Input(**{**fields, **changes})


@pytest.mark.parametrize(("mode", "dry_run"), [("run", False), ("chat", True)])
async def test_only_a_chat_writes_and_tries_commands(settings, mode, dry_run):
    with pytest.raises(ToolError, match="только в чате"):
        await draft_tool.draft(_draft_input(), _ctx(settings, mode, dry_run))

    with pytest.raises(ToolError, match="только в чате"):
        await trial_tool.trial(trial_tool.Input(name="count_lines"), _ctx(settings, mode, dry_run))


async def test_a_bad_draft_names_its_problems(settings):
    with pytest.raises(ToolError) as e:
        await draft_tool.draft(_draft_input(files={"main.py": "def run(args, ctx)\n"}), _ctx(settings))

    assert "main.py, строка 1" in str(e.value)


@pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")
async def test_the_chat_drafts_tries_and_shows_a_command(settings, env):
    (env / "workspace" / "a.txt").write_text("один\nдва\n", encoding="utf-8")
    ctx = _ctx(settings)

    written = await draft_tool.draft(_draft_input(), ctx)
    assert (written.tool, written.version, written.status) == ("ext.count_lines", 1, "draft")

    tried = await trial_tool.trial(trial_tool.Input(name="count_lines", args={"file": "a.txt"}), ctx)
    assert tried.output["lines"] == 2 and tried.output["out"] == "data:outputs/ses_tools/count.txt"

    shown = await show_tool.show(show_tool.Input(name="ext.count_lines"), ctx)
    assert shown.status == "draft" and shown.files["main.py"] == COUNT_LINES

    listed = await list_tool.list_tools(list_tool.Input(), ctx)
    assert listed.tools[0]["draft_version"] == 1 and listed.tools[0]["current_version"] is None
    # The card in the chat finds the draft by the chat that wrote it.
    assert listed.tools[0]["versions"][0]["session_id"] == "ses_tools"
    assert listed.tools[0]["versions"][0]["created_by"] == "chat"


@pytest.mark.skipif(not HAVE_UV, reason="uv is not installed")
async def test_a_draft_changed_behind_its_back_does_not_run(settings):
    ctx = _ctx(settings)
    await draft_tool.draft(_draft_input(), ctx)

    (store.version_dir(settings.storage, "count_lines", 1) / "main.py").write_text("import os\n")

    with pytest.raises(ToolError, match="изменились после сохранения"):
        await trial_tool.trial(trial_tool.Input(name="count_lines", args={"file": "a.txt"}), ctx)
