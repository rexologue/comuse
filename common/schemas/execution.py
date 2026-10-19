"""Wire format between callers (agent-loop, runner) and the executor."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from common.schemas.task import Permissions, SendMode


class ExecContext(BaseModel):
    # chat: interactive call from agent-loop; run: a step of a task run.
    mode: Literal["chat", "run"] = "chat"
    session_id: str | None = None
    run_id: str | None = None
    step_key: str | None = None
    # None = the whole workspace (chat mode); a task run always passes its own.
    permissions: Permissions | None = None
    # run: external actions become drafts waiting for the user unless the task sends.
    send_mode: SendMode = "draft"
    dry_run: bool = False
    # The user already confirmed this exact external action.
    confirmed: bool = False


class ExecuteRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    context: ExecContext = Field(default_factory=ExecContext)


class ExecuteResponse(BaseModel):
    # needs_approval: an external action waits for the user's OK (a draft may exist).
    # needs_input: the tool asks the user a question (user.ask).
    status: Literal["ok", "error", "needs_approval", "needs_input"]
    output: Any = None
    error: str | None = None
    # real | draft | skipped — what actually happened to a side-effecting tool.
    mode: Literal["real", "draft", "skipped"] = "real"
    message: str | None = None
    preview: dict[str, Any] | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
