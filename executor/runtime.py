"""The call pipeline: validate the arguments → check path arguments → load declared
secrets → run with timeout. The executor keeps no log: the response
carries everything (duration, traceback, the tool's log lines and notifications) for
the caller's own log. Whoever calls has already decided the call should happen (the
chat asks the user first; a task was approved as a whole).

The executor knows argument types, not domains: a path-typed argument must stay inside
the workspace, a `secrets` argument names an account it loads. What a task may do is
its approved steps; what to work on is chosen by the tools themselves, and so is what a
dry run means for each of them (the executor only passes the flag on)."""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any

from pydantic import BaseModel, ValidationError

from common.clients.llm_client import LLMClient
from common.schemas.execution import ExecContext, ExecuteRequest, ExecuteResponse
from common.settings import Settings
from common.storage.db import session
from common.storage.models import Account
from common.tools import Credential, ToolContext, ToolError, ToolSpec, path_refs
from common.tools.context import PermissionDenied
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
    ):

        self.settings = settings
        self.registry = registry
        self.secrets  = secrets
        self.llm      = llm

        self.paths = WorkspacePaths(
            workspace_dir=settings.storage.workspace_dir,
            data_dir=settings.storage.data_dir,
        )


    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        started = time.monotonic()
        response = await self._execute(req)
        response.duration_ms = int((time.monotonic() - started) * 1000)

        return response


    async def _execute(self, req: ExecuteRequest) -> ExecuteResponse:
        spec = self.registry.get(req.tool)
        if spec is None:
            return ExecuteResponse(status="error", error=f"Неизвестный инструмент `{req.tool}`")

        try:
            inp = spec.input_model.model_validate(req.args)

        except ValidationError as e:
            return ExecuteResponse(status="error", error=f"Неверные аргументы `{req.tool}`: {_short_errors(e)}")

        try:
            for ref in path_refs(spec, inp):
                self.paths.read_path(ref)

        except PermissionDenied as e:
            return ExecuteResponse(status="error", error=f"Нет доступа: {e}")

        try:
            credentials = {argument: self.load_credential(getattr(inp, argument)) for argument in spec.secrets}

        except ToolError as e:
            return ExecuteResponse(status="error", error=str(e))

        ctx = self._tool_context(req.context, credentials=credentials)

        try:
            raw = await asyncio.wait_for(
                spec.fn(inp, ctx),
                timeout=spec.timeout or self.settings.executor.tool_timeout,
            )
            output = _dump_output(spec, raw)

        except PermissionDenied as e:
            return ExecuteResponse(status="error", error=f"Нет доступа: {e}", logs=ctx.logs)

        except ToolError as e:
            return ExecuteResponse(status="error", error=str(e), logs=ctx.logs)

        except TimeoutError:
            return ExecuteResponse(status="error", error=f"`{spec.name}` не уложился в таймаут")

        except Exception as e:
            log.exception("tool %s crashed", spec.name)
            return ExecuteResponse(
                status="error",
                error=f"Сбой `{spec.name}`: {e!r}",
                traceback=traceback.format_exc(),
                logs=ctx.logs,
                notifications=ctx.notifications,
            )

        return ExecuteResponse(
            status="ok",
            output=output,
            artifacts=ctx.artifacts,
            logs=ctx.logs,
            notifications=ctx.notifications,
        )


    def _tool_context(self, ctx: ExecContext, *, credentials: dict[str, Credential]) -> ToolContext:
        return ToolContext(
            mode=ctx.mode,
            settings=self.settings,
            paths=self.paths,
            llm=self.llm,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            step_key=ctx.step_key,
            dry_run=ctx.dry_run,
            task_settings=ctx.task_settings,
            credentials=credentials,
        )


    def load_credential(self, name: str) -> Credential:
        with session() as db:
            account = db.query(Account).filter(Account.name == name).one_or_none()

        if account is None:
            raise ToolError(f"Аккаунт `{name}` не настроен")

        secret = self.secrets.get(account.secret_ref)
        if secret is None:
            raise ToolError(f"Для аккаунта `{name}` нет сохранённого пароля")

        return Credential(name=account.name, kind=account.kind, config=dict(account.config), secret=secret)


def _dump_output(spec: ToolSpec, raw: Any) -> Any:
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")

    return spec.output_model.model_validate(raw).model_dump(mode="json")


def _short_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
