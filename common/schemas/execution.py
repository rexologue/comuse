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
    args: dict[str, Any] = Field(default_factory=dict)
    context: ExecContext = Field(default_factory=ExecContext)


class ExecuteResponse(BaseModel):
    # needs_input: the tool asks the user a question (user.ask).
    status: Literal["ok", "error", "needs_input"]
    output: Any = None
    error: str | None = None
    # real | draft | skipped — what actually happened to a side-effecting tool.
    mode: Literal["real", "draft", "skipped"] = "real"
    message: str | None = None
    preview: dict[str, Any] | None = None
    artifacts: list[str] = Field(default_factory=list)
    duration_ms: int = 0
