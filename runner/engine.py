"""Executes a frozen TaskSpec step by step; never plans anything itself.

The engine is re-entrant: completed step outputs live in `runs.state`, so a run
paused for the user's answer is resumed by walking the spec again and skipping
everything already done. Step keys are paths: `sort[2].move`."""

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
from common.storage.models import Run, StepRun, Task, TaskVersion
from common.storage.queries import run_detail
from common.utils import truncate, utcnow


log = logging.getLogger("engine")

_RETRY_BACKOFF_SEC = 2.0

# What a run started without trigger data (a dry run, "run now") sees, so steps such
# as `foreach: trigger.messages` iterate over nothing instead of failing.
_EMPTY_TRIGGER: dict[str, dict[str, Any]] = {
    "new_email": {"messages": []},
    "file_appeared": {"files": []},
}


class _Pause(Exception):
    """The run waits for an approval; state is saved, the walk unwinds."""


class _Stop(Exception):
    """The run ends early without being a failure (nothing to do, user declined)."""

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
        """Run (or continue) until done, paused or failed; returns the run view."""
        with session() as db:
            run = db.get(Run, run_id)
            version = db.get(TaskVersion, run.task_version_id)
            task = db.get(Task, run.task_id)

        # A run cancelled while waiting has nothing left to do.
        if run.status in ("succeeded", "failed", "cancelled"):
            return run_detail(run_id)

        spec = TaskSpec.model_validate(version.spec)
        state = _State(run.state or {})

        self.update(run_id, status="running", started_at=run.started_at or utcnow())
        if not state.started:
            state.started = True
            await self.main.publish("run.started", {"run_id": run_id, "task_id": run.task_id, "title": task.title, "dry_run": run.dry_run})

        walk = _Walk(self, run, spec, state, send_mode=task.send_mode)

        try:
            await walk.execute()
            status, error = "succeeded", None
            state.result = {"outputs": walk.last_output, "artifacts": state.artifacts}

        except _Pause:
            self.update(run_id, status="waiting", state=state.dump())
            await self.main.publish("run.waiting", {"run_id": run_id, "task_id": run.task_id, "title": task.title, "pending": state.pending})
            return run_detail(run_id)

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

        self.update(run_id, status=status, error=error, result=state.result, state=state.dump(), finished_at=utcnow())
        self._cancelled.discard(run_id)

        await self.main.publish(
            "run.finished",
            {"run_id": run_id, "task_id": run.task_id, "title": task.title, "status": status, "error": error, "dry_run": run.dry_run},
        )

        return run_detail(run_id)


    def resume(self, run_id: str, approval_id: str, approve: bool, answer: Any) -> None:
        with session() as db:
            run = db.get(Run, run_id)

        if run is None:
            raise ApiError(status_code=404, message=f"Run `{run_id}` not found")

        state = _State(run.state or {})
        pending = state.pending

        if run.status != "waiting" or not pending or pending.get("approval_id") != approval_id:
            raise ApiError(status_code=409, message=f"Run `{run_id}` is not waiting for approval `{approval_id}`")

        state.answers[pending["key"]] = {**pending, "approve": approve, "answer": answer}
        state.pending = None

        self.update(run_id, status="queued", state=state.dump())


    def update(self, run_id: str, **fields: Any) -> None:
        with session() as db:
            run = db.get(Run, run_id)
            for key, value in fields.items():
                setattr(run, key, value)

            db.commit()


    def is_cancelled(self, run_id: str) -> bool:
        return run_id in self._cancelled


class _State:
    """JSON-serializable progress of a run."""

    def __init__(self, raw: dict[str, Any]):
        self.started: bool = raw.get("started", False)
        self.trigger_data: dict[str, Any] = raw.get("trigger_data", {})
        self.done: dict[str, Any] = raw.get("done", {})
        self.answers: dict[str, Any] = raw.get("answers", {})
        self.pending: dict[str, Any] | None = raw.get("pending")
        self.artifacts: list[str] = raw.get("artifacts", [])
        self.result: dict[str, Any] | None = raw.get("result")


    def dump(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "trigger_data": self.trigger_data,
            "done": self.done,
            "answers": self.answers,
            "pending": self.pending,
            "artifacts": self.artifacts,
            "result": self.result,
        }


class _Walk:
    def __init__(self, engine: RunEngine, run: Run, spec: TaskSpec, state: _State, *, send_mode: str):

        self.engine    = engine
        self.run       = run
        self.spec      = spec
        self.state     = state
        self.send_mode = send_mode

        self.last_output: Any = None


    async def execute(self) -> None:
        ctx: dict[str, Any] = {
            "today": date.today(),
            "now": datetime.now(),
            "trigger": {**_EMPTY_TRIGGER.get(self.spec.trigger.type, {}), **self.state.trigger_data},
        }

        for name, expr in self.spec.variables.items():
            try:
                ctx[name] = templating.render_str(expr, ctx)

            except TemplateError as e:
                raise _Fail(f"Переменная `{name}`: {e}") from e

        await self._steps(self.spec.steps, ctx, prefix="")


    async def _steps(self, steps: list[Step], ctx: dict[str, Any], prefix: str) -> None:
        for step in steps:
            if self.engine.is_cancelled(self.run.id):
                raise _Stop("cancelled", "Отменено пользователем")

            key = f"{prefix}{step.id}"

            if key in self.state.done:
                ctx[step.id] = {"output": self.state.done[key]["output"]}
                continue

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

        answer = self.state.answers.get(key)

        if answer is not None:
            resolved, output, status = self._apply_answer(answer)
            if resolved:
                return self._finish(key, step, output, status=status, inputs=args)

        confirmed = bool(answer and answer["reason"] == "confirm" and answer["approve"])
        attempts = 1 + (step.retries if step.on_error == "retry" else 0)

        for attempt in range(1, attempts + 1):
            response = await self._call(step, key, args, confirmed=confirmed, attempt=attempt)
            status = response["status"]
            self.state.artifacts += [a for a in response.get("artifacts", []) if a not in self.state.artifacts]

            if status == "ok":
                return await self._check_empty(step, key, args, response["output"])

            if status == "needs_approval":
                await self._ask(
                    key,
                    reason="confirm",
                    kind="confirm_action",
                    payload={
                        "step": key,
                        "tool": step.tool,
                        "message": response.get("message"),
                        "preview": response.get("preview"),
                    },
                )

            if status == "needs_input":
                await self._ask(
                    key,
                    reason="input",
                    kind="ask_user",
                    payload={"step": key, "question": response.get("message"), "options": (response.get("preview") or {}).get("options", [])},
                )

            if attempt < attempts:
                await asyncio.sleep(_RETRY_BACKOFF_SEC * attempt)

        # The failed attempt is already in step_runs (written by _call).
        return await self._handle_error(step, key, args, response.get("error") or "неизвестная ошибка", record=False)


    def _apply_answer(self, answer: dict[str, Any]) -> tuple[bool, Any, str]:
        """(resolved, output, step status) for a step that already has the user's answer."""
        reason, approve = answer["reason"], answer["approve"]

        if reason == "confirm":
            # Approved: run for real below. Declined: the step is skipped.
            return (False, None, "ok") if approve else (True, None, "skipped")

        if reason == "input":
            if not approve:
                raise _Stop("cancelled", "Пользователь не ответил на вопрос")

            return True, {"answer": answer.get("answer") or ""}, "ok"

        if reason == "error":
            if not approve:
                raise _Fail(f"Шаг `{answer['key']}` завершился ошибкой: {answer['payload'].get('error')}")

            return True, None, "skipped"

        if reason == "empty":
            if not approve:
                raise _Stop("cancelled", f"Остановлено: шаг `{answer['key']}` ничего не нашёл")

            return True, answer.get("output"), "ok"

        return False, None, "ok"


    async def _check_empty(self, step: Step, key: str, args: dict[str, Any], output: Any) -> Any:
        if step.on_empty == "continue" or not _is_empty(output):
            return self._finish(key, step, output, status="ok", inputs=args)

        if step.on_empty == "fail":
            raise _Fail(f"Шаг `{key}` ничего не вернул")

        if step.on_empty == "skip" or self.run.dry_run:
            self._finish(key, step, output, status="ok", inputs=args)
            raise _Stop("succeeded", f"Шаг `{key}` ничего не нашёл — делать нечего")

        await self._ask(
            key,
            reason="empty",
            kind="ask_user",
            payload={"step": key, "question": f"Шаг «{step.title or step.tool}» ничего не нашёл. Продолжить выполнение задачи?"},
            output=output,
        )


    async def _handle_error(self, step: Step, key: str, args: dict[str, Any] | None, error: str, *, record: bool) -> Any:
        if record:
            self._record(key, step, status="error", inputs=args, output=None, error=error)

        if step.on_error == "skip":
            return self._finish(key, step, None, status="skipped", inputs=args, record=False)

        if step.on_error == "ask_user" and not self.run.dry_run:
            await self._ask(
                key,
                reason="error",
                kind="ask_user",
                payload={"step": key, "error": error, "question": f"Шаг «{step.title or step.tool}» завершился ошибкой: {error}. Пропустить его и продолжить?"},
            )

        raise _Fail(f"Шаг `{key}`: {error}")


    async def _ask(self, key: str, *, reason: str, kind: str, payload: dict[str, Any], output: Any = None) -> None:
        approval = await self.engine.main.post(
            "/internal/approvals",
            json={"kind": kind, "run_id": self.run.id, "task_id": self.run.task_id, "payload": payload},
        )

        self.state.pending = {
            "key": key,
            "reason": reason,
            "approval_id": approval["id"],
            "payload": payload,
            "output": output,
        }

        raise _Pause()


    async def _call(self, step: Step, key: str, args: dict[str, Any], *, confirmed: bool, attempt: int) -> dict[str, Any]:
        context = {
            "mode": "run",
            "run_id": self.run.id,
            "step_key": key,
            "permissions": self.spec.permissions.model_dump(),
            "send_mode": self.send_mode,
            "dry_run": self.run.dry_run,
            "confirmed": confirmed,
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
            status={"ok": "ok", "needs_approval": "waiting", "needs_input": "waiting"}.get(response["status"], "error"),
            inputs=args,
            output=response.get("output"),
            error=response.get("error"),
            attempt=attempt,
        )

        return response


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

        self.engine.update(self.run.id, state=self.state.dump())

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
    ) -> None:
        with session() as db:
            db.add(
                StepRun(
                    run_id=self.run.id,
                    step_key=key,
                    step_id=step.id,
                    tool=step.tool,
                    attempt=attempt,
                    status=status,
                    input=inputs,
                    output=_clip(output),
                    error=error,
                    finished_at=utcnow(),
                )
            )
            db.commit()


def _is_empty(output: Any) -> bool:
    """`{"files": []}` counts as empty: a single-field result whose value is empty."""
    if output in (None, "", [], {}):
        return True

    if isinstance(output, dict) and output:
        return all(value in (None, "", [], {}) for value in output.values())

    return False


def _clip(output: Any) -> Any:
    """Big outputs (document text) are stored cut; the full value stays in the state."""
    text = truncate(output, 20000)

    return output if len(text) < 20000 else {"truncated": text}
