"""Executes a frozen TaskSpec step by step, start to finish; never plans anything and
never waits for a person (whatever needs one, a tool leaves outside the run: a draft,
a file to check). Step keys are paths: `sort[2].move`.

Everything about a run goes into its one log (`run_log`, by task_id + run_id): the
start with its version, settings and variables, every step with its arguments,
output, error and the tool's own lines, notifications, the end."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from jinja2 import TemplateError

from common import templating
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.schemas.task import Step, TaskSpec
from common.storage.db import session
from common.storage.models import RUN_FINISHED, Run, RunLog, Task, TaskVersion
from common.storage.queries import run_detail
from common.utils import truncate, utcnow


log = logging.getLogger("engine")

_RETRY_BACKOFF_SEC = 2.0


class _Stop(Exception):
    """The run ends early without being a failure (nothing to do, cancelled)."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)

        self.status = status
        self.reason = reason


class _Fail(Exception):
    pass


class RunEngine:
    def __init__(self, main: MainClient):

        self.main = main

        self._cancelled: set[str] = set()


    def cancel(self, run_id: str) -> None:
        self._cancelled.add(run_id)


    async def execute(self, run_id: str) -> dict[str, Any]:
        """Run to the end (or the first failure); returns the run view."""
        with session() as db:
            run = db.get(Run, run_id)
            version = db.get(TaskVersion, run.task_version_id)
            task = db.get(Task, run.task_id)

        # Cancelled before it started.
        if run.status in RUN_FINISHED:
            return run_detail(run_id)

        spec = TaskSpec.model_validate(version.spec)
        state = _State()

        self.update(run_id, status="running", started_at=utcnow())
        await self.main.publish("run.started", {"run_id": run_id, "task_id": run.task_id, "title": task.title, "dry_run": run.dry_run})

        journal = _Journal(run)
        walk = _Walk(self, run, spec, state, journal, title=task.title, task_settings=task.settings or {})

        try:
            await walk.execute()
            status, error = "succeeded", None
            state.result = {"outputs": walk.last_output, "artifacts": state.artifacts}

        except _Stop as e:
            status, error = e.status, None
            state.result = {"stopped": e.reason, "artifacts": state.artifacts}

        except _Fail as e:
            status, error = "failed", str(e)
            state.result = {"artifacts": state.artifacts}

        except Exception as e:
            log.exception("run %s crashed", run_id)
            status, error = "failed", f"Сбой исполнителя: {e!r}"
            state.result = {"artifacts": state.artifacts}

        if status == "succeeded" and not run.dry_run and _found_nothing(state):
            status = "empty"

        journal.add("end", {"status": status, "error": error, "result": state.result})
        self.update(run_id, status=status, error=error, result=state.result, finished_at=utcnow())
        self._cancelled.discard(run_id)

        await self.main.publish(
            "run.finished",
            {"run_id": run_id, "task_id": run.task_id, "title": task.title, "status": status, "error": error, "dry_run": run.dry_run},
        )

        return run_detail(run_id)


    def update(self, run_id: str, **fields: Any) -> None:
        with session() as db:
            run = db.get(Run, run_id)
            for key, value in fields.items():
                setattr(run, key, value)

            db.commit()


    def is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled


class _State:
    """What a run has produced so far: step results by key and the files it made."""

    def __init__(self):
        self.done: dict[str, Any] = {}
        self.artifacts: list[str] = []
        self.result: dict[str, Any] | None = None


class _Journal:
    """Appends to the run's log; entries are numbered in the order they happen."""

    def __init__(self, run: Run):

        self.run_id  = run.id
        self.task_id = run.task_id
        self.seq     = 0


    def add(self, kind: str, data: dict[str, Any], *, step_key: str | None = None) -> None:
        self.seq += 1

        with session() as db:
            db.add(RunLog(run_id=self.run_id, task_id=self.task_id, seq=self.seq, kind=kind, step_key=step_key, data=data))
            db.commit()


class _Walk:
    def __init__(
        self,
        engine: RunEngine,
        run: Run,
        spec: TaskSpec,
        state: _State,
        journal: _Journal,
        *,
        title: str,
        task_settings: dict[str, Any],
    ):

        self.engine        = engine
        self.run           = run
        self.spec          = spec
        self.state         = state
        self.journal       = journal
        self.title         = title
        self.task_settings = task_settings

        self.last_output: Any = None


    async def execute(self) -> None:
        ctx: dict[str, Any] = {
            "today": date.today(),
            "now": datetime.now(),
        }

        for name, expr in self.spec.variables.items():
            try:
                ctx[name] = templating.render_str(expr, ctx)

            except TemplateError as e:
                self._log_start(ctx)
                raise _Fail(f"Переменная `{name}`: {e}") from e

        self._log_start(ctx)
        await self._steps(self.spec.steps, ctx, prefix="")


    def _log_start(self, ctx: dict[str, Any]) -> None:
        self.journal.add(
            "start",
            {
                "title": self.title,
                "task_version_id": self.run.task_version_id,
                "trigger": self.run.trigger,
                "dry_run": self.run.dry_run,
                "settings": self.task_settings,
                "variables": templating.jsonable({name: ctx[name] for name in self.spec.variables if name in ctx}),
            },
        )


    async def _steps(self, steps: list[Step], ctx: dict[str, Any], prefix: str) -> None:
        for step in steps:
            if self.engine.is_cancelled(self.run.id):
                raise _Stop("cancelled", "Отменено пользователем")

            key = f"{prefix}{step.id}"

            if step.when and not self._evaluate(step.when, ctx, key):
                self._finish(key, step, None, status="skipped", inputs=None)
                ctx[step.id] = {"output": None}
                continue

            if step.foreach is not None:
                ctx[step.id] = {"output": await self._foreach(step, ctx, key)}
                continue

            output = await self._tool_step(step, key, ctx)
            ctx[step.id] = {"output": output}
            self.last_output = output


    async def _foreach(self, step: Step, ctx: dict[str, Any], key: str) -> list[Any]:
        items = self._evaluate(step.foreach, ctx, key)
        if not isinstance(items, list):
            raise _Fail(f"Шаг `{key}`: foreach ожидает список, получено {type(items).__name__}")

        results = []
        for i, item in enumerate(items):
            inner = {**ctx, step.as_: item}
            await self._steps(step.steps or [], inner, prefix=f"{key}[{i}].")
            results.append({child.id: inner.get(child.id, {}).get("output") for child in step.steps or []})

        self._finish(key, step, results, status="ok", inputs={"items": len(items)})

        return results


    async def _tool_step(self, step: Step, key: str, ctx: dict[str, Any]) -> Any:
        try:
            args = templating.jsonable(templating.render(step.args, ctx))

        except TemplateError as e:
            return await self._handle_error(step, key, None, f"Не удалось подставить аргументы: {e}", record=True)

        attempts = 1 + (step.retries if step.on_error == "retry" else 0)

        for attempt in range(1, attempts + 1):
            response = await self._call(step, key, args, attempt=attempt)
            status = response["status"]
            self.state.artifacts += [a for a in response.get("artifacts", []) if a not in self.state.artifacts]

            await self._deliver(response.get("notifications") or [], key)

            if status == "ok":
                return await self._check_empty(step, key, args, response["output"])

            if attempt < attempts:
                await asyncio.sleep(_RETRY_BACKOFF_SEC * attempt)

        # The failed attempt is already in the log (written by _call).
        return await self._handle_error(step, key, args, response.get("error") or "неизвестная ошибка", record=False)


    async def _check_empty(self, step: Step, key: str, args: dict[str, Any], output: Any) -> Any:
        if step.on_empty == "continue" or not _is_empty(output):
            return self._finish(key, step, output, status="ok", inputs=args)

        if step.on_empty == "fail":
            raise _Fail(f"Шаг `{key}` ничего не вернул")

        # skip: nothing to do this time, which is not a failure.
        self._finish(key, step, output, status="ok", inputs=args)
        raise _Stop("succeeded", f"Шаг `{key}` ничего не нашёл — делать нечего")


    async def _handle_error(self, step: Step, key: str, args: dict[str, Any] | None, error: str, *, record: bool) -> Any:
        if record:
            self._record(key, step, status="error", inputs=args, output=None, error=error)

        if step.on_error == "skip":
            return self._finish(key, step, None, status="skipped", inputs=args, record=False)

        raise _Fail(f"Шаг `{key}`: {error}")


    async def _call(self, step: Step, key: str, args: dict[str, Any], *, attempt: int) -> dict[str, Any]:
        context = {
            "mode": "run",
            "run_id": self.run.id,
            "step_key": key,
            "task_settings": self.task_settings,
            "dry_run": self.run.dry_run,
        }

        try:
            response = await self.engine.main.post(
                "/executor/execute",
                json={"tool": step.tool, "args": args, "context": context},
            )

        except ApiError as e:
            response = {"status": "error", "error": e.message}

        self._record(
            key,
            step,
            status="ok" if response["status"] == "ok" else "error",
            inputs=args,
            output=response.get("output"),
            error=response.get("error"),
            attempt=attempt,
            extra={
                "mode": response.get("mode"),
                "duration_ms": response.get("duration_ms"),
                "traceback": response.get("traceback"),
                "logs": response.get("logs") or [],
            },
        )

        return response


    async def _deliver(self, messages: list[str], key: str) -> None:
        """Notifications go to the log and, outside a dry run, to the task's chat."""
        for message in messages:
            self.journal.add("notify", {"message": message}, step_key=key)

            if self.run.dry_run:
                continue

            try:
                await self.engine.main.post(
                    "/internal/notify",
                    json={"task_id": self.run.task_id, "run_id": self.run.id, "title": self.title, "message": message},
                )

            except ApiError as e:
                log.warning("notification of run %s not delivered: %s", self.run.id, e.message)


    def _evaluate(self, expr: str, ctx: dict[str, Any], key: str) -> Any:
        try:
            return templating.evaluate(expr, ctx)

        except TemplateError as e:
            raise _Fail(f"Шаг `{key}`: выражение {expr!r} не вычисляется: {e}") from e


    def _finish(self, key: str, step: Step, output: Any, *, status: str, inputs: Any, record: bool = True) -> Any:
        self.state.done[key] = {"output": output, "status": status}

        if record and step.foreach is not None:
            self._record(key, step, status=status, inputs=inputs, output=output)

        if record and status == "skipped":
            self._record(key, step, status=status, inputs=inputs, output=None)

        return output


    def _record(
        self,
        key: str,
        step: Step,
        *,
        status: str,
        inputs: Any,
        output: Any,
        error: str | None = None,
        attempt: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> None:
        data = {
            "step_id": step.id,
            "tool": step.tool,
            "attempt": attempt,
            "status": status,
            "input": inputs,
            "output": _clip(output),
            "error": error,
            **(extra or {}),
        }
        self.journal.add("step", {k: v for k, v in data.items() if v not in (None, [], {}) or k in ("status", "output")}, step_key=key)


def _found_nothing(state: _State) -> bool:
    """Every step ran and came back empty: a scheduled check that had nothing to handle.
    A skipped step (failed with on_error=skip) is something worth seeing."""
    return bool(state.done) and all(done["status"] == "ok" and _is_empty(done["output"]) for done in state.done.values())


def _is_empty(output: Any) -> bool:
    """`{"files": []}` counts as empty: a single-field result whose value is empty."""
    if output in (None, "", [], {}):
        return True

    if isinstance(output, dict) and output:
        return all(value in (None, "", [], {}) for value in output.values())

    return False


def _clip(output: Any) -> Any:
    """Big outputs (document text) are logged cut."""
    text = truncate(output, 20000)

    return output if len(text) < 20000 else {"truncated": text}
