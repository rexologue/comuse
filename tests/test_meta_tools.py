"""Agent meta tools: approving a task stays with the user, the agent answers nothing."""

from __future__ import annotations

from agent_loop import meta_tools


def test_agent_has_no_task_approve():
    assert "task_approve" not in meta_tools.NAMES


def test_agent_cannot_answer_approvals():
    assert not {"approvals_list", "approval_answer"} & meta_tools.NAMES


def test_every_meta_tool_has_a_title_for_the_user():
    from agent_loop import meta_tools

    assert set(meta_tools.TITLES) == meta_tools.NAMES
    assert meta_tools.ASKING <= meta_tools.NAMES
