"""Executor side-effect policy: chat asks first, dry runs suppress effects, task runs
leave drafts for approval unless the user switched the task to sending."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.schemas.execution import ExecContext
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


def test_run_leaves_a_draft_by_default():
    decision = _decide("mail.send", mode="run")
    assert decision.ask_first and decision.draft


def test_run_in_send_mode_sends():
    decision = _decide("mail.send", mode="run", send_mode="send")
    assert not decision.ask_first and not decision.draft


def test_spec_cannot_carry_a_send_mode():
    with pytest.raises(ValidationError):
        TaskSpec.model_validate({"title": "x", "approval": {"mode": "never"}, "steps": [{"id": "a", "tool": "fs.list"}]})
