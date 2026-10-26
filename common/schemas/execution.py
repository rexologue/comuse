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
    # The values the user set for this step's tool (its own, else the task's: see
    # common/tools/settings.py); what is not here takes the tool's default.
    tool_settings: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False


class ExecuteRequest(BaseModel):
    tool: str
    # An own command: the approved version to run, as the task step pins it; None — the
    # current one (the chat). Built-in tools have no versions.
    version: int | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    context: ExecContext = Field(default_factory=ExecContext)


class ExecuteResponse(BaseModel):
    status: Literal["ok", "error"]
    output: Any = None
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
    # The version of an external tool (ext.*) that ran: the chat trace and run log keep it.
    version: int | None = None
    # The tool did not finish in time: what it did meanwhile is unknown, so it is not retried.
    timed_out: bool = False
    # For the caller's log (the executor keeps none): a crash's traceback, what the
    # tool wrote with ctx.log(), and messages for the user it sent with ctx.notify().
    traceback: str | None = None
    logs: list[str] = Field(default_factory=list)
    notifications: list[str] = Field(default_factory=list)
