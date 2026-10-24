"""Wire format between callers (agent-loop, runner) and the executor."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field



class ExecContext(BaseModel):
    # chat: interactive call from agent-loop; run: a step of a task run.
    mode: Literal["chat", "run"] = "chat"
    session_id: str | None = None
    run_id: str | None = None
    step_key: str | None = None
    # The task's domain settings sections; each tool reads its own domain's.
    task_settings: dict[str, dict[str, Any]] = Field(default_factory=dict)
    dry_run: bool = False


class ExecuteRequest(BaseModel):
    tool: str
    # External tools (ext.*): the approved version to run, as the task step pins it;
    # None — the current one (the chat). Internal tools have no versions.
    version: int | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    context: ExecContext = Field(default_factory=ExecContext)


class ExecuteResponse(BaseModel):
    status: Literal["ok", "error"]
    output: Any = None
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    # For the caller's log (the executor keeps none): a crash's traceback, what the
    # tool wrote with ctx.log(), and messages for the user it sent with ctx.notify().
    traceback: str | None = None
    logs: list[str] = Field(default_factory=list)
    notifications: list[str] = Field(default_factory=list)
