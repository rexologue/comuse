"""The call pipeline: the tool is the one the task was approved with → validate the
arguments → check path arguments → load declared secrets → run with timeout. The executor keeps no log: the response
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

from pydantic import ValidationError

from common import tunables
from common.schemas.execution import ExecContext, ExecuteRequest, ExecuteResponse
from common.secrets import SecretStore
from common.settings import Settings
from common.storage.db import session
from common.storage.models import Account
from common.tools import Credential, ToolContext, ToolError, path_refs
from common.tools.context import PermissionDenied
from executor.paths import WorkspacePaths
from executor.registry import ToolRegistry


log = logging.getLogger("runtime")


class ExecutorRuntime:
    def __init__(
        self,
        settings: Settings,
        registry: ToolRegistry,
        secrets: SecretStore,
    ):

        self.settings = settings
        self.registry = registry
        self.secrets  = secrets


    async def execute(self, req: ExecuteRequest) -> ExecuteResponse:
        started = time.monotonic()
        response = await self._execute(req)
        response.duration_ms = int((time.monotonic() - started) * 1000)

        return response


    async def _execute(self, req: ExecuteRequest) -> ExecuteResponse:
        # Looked up once, with its manifest: the tool that runs is the one checked.
        spec, manifest = self.registry.get(req.tool), self.registry.manifest(req.tool)

        if spec is None:
            group = req.tool.split(".", 1)[0]
            if group in self.registry.unavailable:
                # Not gone: its plugin's server is not running. Not a change to review either.
                return ExecuteResponse(status="error", error=f"Плагин «{group}» не запущен: {self.registry.unavailable[group]}")

            if req.hash is not None:
                return ExecuteResponse(status="error", error=f"Инструмента `{req.tool}` больше нет", tool_changed=True)

            return ExecuteResponse(status="error", error=f"Неизвестный инструмент `{req.tool}`")

        if req.hash is not None and manifest["hash"] != req.hash:
            return ExecuteResponse(status="error", error=f"`{req.tool}` изменился после одобрения задачи: шаг не запущен", tool_changed=True)

        try:
            inp = spec.parse(req.args)

        except ValidationError as e:
            return ExecuteResponse(status="error", error=f"Неверные аргументы `{req.tool}`: {_short_errors(e)}")

        except ToolError as e:
            return ExecuteResponse(status="error", error=f"Неверные аргументы `{req.tool}`: {e}")

        # Per call: the workspace may change through /settings while the executor runs.
        paths = WorkspacePaths(tunables.get("workspace_dir"), self.settings.storage.data_dir)

        try:
            for ref in path_refs(spec, inp):
                paths.read_path(ref)

        except PermissionDenied as e:
            return ExecuteResponse(status="error", error=f"Нет доступа: {e}")

        try:
            # A built-in tool's input is its model, a plugin's a dict.
            # An optional one left empty (tools.try of a plugin without accounts) loads nothing.
            named = {argument: inp.get(argument) if isinstance(inp, dict) else getattr(inp, argument) for argument in spec.secrets}
            credentials = {argument: self.load_credential(name) for argument, name in named.items() if name is not None}

        except ToolError as e:
            return ExecuteResponse(status="error", error=str(e))

        try:
            tool_settings = _tool_settings(spec, req.context.tool_settings)

        except ValidationError as e:
            return ExecuteResponse(status="error", error=f"Настройки задачи для `{spec.name}` не подходят: {_short_errors(e)}")

        ctx = self._tool_context(req.context, paths=paths, credentials=credentials, tool_settings=tool_settings)

        # Whatever the tool managed to log or tell the user before it failed belongs in
        # the caller's log too.
        said = {"logs": ctx.logs, "notifications": ctx.notifications}

        try:
            # Building a command's .venv on its first call is not the command's time.
            await spec.prepare()

            output = await asyncio.wait_for(
                spec.call(inp, ctx),
                timeout=spec.timeout or tunables.get("tool_timeout_sec"),
            )

        except PermissionDenied as e:
            return ExecuteResponse(status="error", error=f"Нет доступа: {e}", **said)

        except ToolError as e:
            return ExecuteResponse(status="error", error=str(e), **said)

        except TimeoutError:
            return ExecuteResponse(status="error", error=f"`{spec.name}` не уложился в таймаут", timed_out=True, **said)

        except Exception as e:
            log.exception("tool %s crashed", spec.name)
            return ExecuteResponse(status="error", error=f"Сбой `{spec.name}`: {e!r}", traceback=traceback.format_exc(), **said)

        return ExecuteResponse(
            status="ok",
            output=output,
            artifacts=ctx.artifacts,
            moved=ctx.moved,
            logs=ctx.logs,
            notifications=ctx.notifications,
        )


    def _tool_context(self, ctx: ExecContext, *, paths: WorkspacePaths, credentials: dict[str, Credential], tool_settings: Any) -> ToolContext:
        return ToolContext(
            mode=ctx.mode,
            settings=self.settings,
            paths=paths,
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            step_key=ctx.step_key,
            dry_run=ctx.dry_run,
            tool_settings=tool_settings,
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


def _short_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())


def _tool_settings(spec, values: dict[str, Any]) -> Any:
    """The tool's settings model with the values set for this step; defaults for the
    rest (and in chat, where nothing is set)."""
    model = getattr(spec, "settings_model", None)
    if model is None:
        return None

    return model.model_validate({key: value for key, value in values.items() if key in model.model_fields})
