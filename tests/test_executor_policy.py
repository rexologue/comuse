"""Executor side-effect policy: chat asks first, dry runs suppress effects, the first
runs of a task produce drafts that wait for approval."""

from __future__ import annotations

from common.schemas.execution import ExecContext
from common.schemas.task import ApprovalPolicy
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


def test_first_runs_draft_then_real():
    policy = ApprovalPolicy(mode="draft_first_n", n=2)

    early = _decide("mail.send", mode="run", approval=policy, prior_runs=1)
    assert early.ask_first and early.draft

    later = _decide("mail.send", mode="run", approval=policy, prior_runs=2)
    assert not later.ask_first


def test_always_policy_keeps_asking():
    decision = _decide("mail.send", mode="run", approval=ApprovalPolicy(mode="always"), prior_runs=50)
    assert decision.ask_first
