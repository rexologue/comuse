"""The call pipeline: validate → side-effect policy → run with timeout → log.

What a task may do is its approved steps; limits on values known only at run time
are arguments of the tools themselves (mail.send's allow_to). Paths always stay
inside the user's workspace."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from common.clients.llm_client import LLMClient
from common.clients.main_client import MainClient
from common.schemas.execution import ExecContext, ExecuteRequest, ExecuteResponse
from common.settings import Settings
from common.storage.db import session
from common.storage.models import Account, ToolCall
from common.tools import NeedsInput, ToolContext, ToolError, ToolSpec
from common.tools.context import MailAccount, PermissionDenied
from common.utils import truncate
from executor.paths import WorkspacePaths
from executor.registry import ToolRegistry
from executor.secrets import SecretStore


log = logging.getLogger("runtime")


class ExecutorRuntime:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        secrets: SecretStore,
        llm: LLMClient,
        main: MainClient,
    ):

        self.settings = settings
        self.registry = registry
        self.secrets  = secrets
        self.llm      = llm
        self.main     = main


    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        started = time.monotonic()
        response = await self._execute(req)
        response.duration_ms = int((time.monotonic() - started) * 1000)

        self._log_call(req, response)

        return response


    async def _execute(self, req: ExecuteRequest) -> ExecuteResponse:
        spec = self.registry.get(req.tool)
        if spec is None:
            return ExecuteResponse(status="error", error=f"Неизвестный инструмент `{req.tool}`")

        try:
            inp = spec.input_model.model_validate(req.args)

        except ValidationError as e:
            return ExecuteResponse(status="error", error=f"Неверные аргументы `{req.tool}`: {_short_errors(e)}")

        decision = self._decide(spec, req.context)
        if decision.skip:
            return ExecuteResponse(status="ok", mode="skipped", message=decision.message)

        if decision.ask_first:
            return ExecuteResponse(
                status="needs_approval",
                message=decision.message,
                preview={"tool": spec.name, "title": spec.title, "args": req.args},
            )

        ctx = self._tool_context(req.context, draft=decision.draft)

        try:
            raw = await asyncio.wait_for(
                spec.fn(inp, ctx),
                timeout=spec.timeout or self.settings.executor.tool_timeout,
            )
            output = _dump_output(spec, raw)

        except NeedsInput as e:
            if req.context.dry_run:
                return ExecuteResponse(status="ok", mode="skipped", message=f"Вопрос пользователю пропущен в пробном запуске: {e.question}")

            return ExecuteResponse(status="needs_input", message=e.question, preview={"options": e.options})

        except PermissionDenied as e:
            return ExecuteResponse(status="error", error=f"Нет доступа: {e}")

        except ToolError as e:
            return ExecuteResponse(status="error", error=str(e))

        except TimeoutError:
            return ExecuteResponse(status="error", error=f"`{spec.name}` не уложился в таймаут")

        except Exception as e:
            log.exception("tool %s crashed", spec.name)
            return ExecuteResponse(status="error", error=f"Сбой `{spec.name}`: {e!r}")

        mode = "draft" if decision.draft else "real"

        return ExecuteResponse(status="ok", output=output, mode=mode, artifacts=ctx.artifacts)


    def _decide(self, spec: ToolSpec, ctx: ExecContext) -> _Decision:
        """How a side effect is allowed to happen for this call. A task run never waits
        here: the user approved the task itself, and how its external actions behave
        (draft or real) is up to each tool and its domain settings."""
        if ctx.dry_run:
            if spec.side_effect in ("write", "external") and spec.dry_run == "skip":
                return _Decision(skip=True, message="Пропущено в пробном запуске")

            return _Decision(draft=spec.dry_run == "draft")

        if ctx.mode == "chat" and spec.side_effect == "external" and not ctx.confirmed:
            return _Decision(ask_first=True, message=f"«{spec.title}» требует подтверждения пользователя")

        return _Decision()


    def _tool_context(self, ctx: ExecContext, *, draft: bool) -> ToolContext:
        paths = WorkspacePaths(
            workspace_dir=self.settings.storage.workspace_dir,
            data_dir=self.settings.storage.data_dir,
        )

        return ToolContext(
            mode=ctx.mode,
            settings=self.settings,
            paths=paths,
            llm=self.llm,
            get_account=self._get_account,
            publish=self.main.publish,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            step_key=ctx.step_key,
            dry_run=ctx.dry_run,
            draft=draft,
            task_settings=ctx.task_settings,
        )


    def _get_account(self, name: str) -> MailAccount:
        with session() as db:
            account = db.query(Account).filter(Account.name == name).one_or_none()

        if account is None:
            raise ToolError(f"Аккаунт `{name}` не настроен")

        secret = self.secrets.get(account.secret_ref)
        if secret is None:
            raise ToolError(f"Для аккаунта `{name}` нет сохранённого пароля")

        return MailAccount(name=account.name, config=dict(account.config), secret=secret)


    def _log_call(self, req: ExecuteRequest, response: ExecuteResponse) -> None:
        ctx = req.context

        with session() as db:
            db.add(
                ToolCall(
                    tool=req.tool,
                    mode=ctx.mode,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    step_key=ctx.step_key,
                    # Arguments never carry secrets (accounts are referenced by name).
                    args=_clip_args(req.args),
                    status=response.status,
                    duration_ms=response.duration_ms,
                    error=response.error,
                )
            )
            db.commit()


class _Decision:
    __slots__ = ("skip", "draft", "ask_first", "message")

    def __init__(self, *, skip: bool = False, draft: bool = False, ask_first: bool = False, message: str | None = None):
        self.skip      = skip
        self.draft     = draft
        self.ask_first = ask_first
        self.message   = message


def _dump_output(spec: ToolSpec, raw: Any) -> Any:
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")

    return spec.output_model.model_validate(raw).model_dump(mode="json")


def _short_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())


def _clip_args(args: dict[str, Any]) -> dict[str, Any]:
    return {key: truncate(value, 500) if not isinstance(value, (int, float, bool)) else value for key, value in args.items()}
