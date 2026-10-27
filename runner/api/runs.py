from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import RUN_FINISHED, Run, Task, TaskVersion
from common.storage.queries import run_detail, run_dict
from common.utils import new_id


router = APIRouter()

# A synchronous (wait=true) run, e.g. a dry run asked in chat, gives up after this.
_WAIT_TIMEOUT_SEC = 600


class RunBody(BaseModel):
    task_id: str | None = None
    task_version_id: str | None = None
    trigger: str = "manual"
    dry_run: bool = False
    wait: bool = False


@router.post("/runs", summary="Start a run (dry runs may target a draft version)")
async def start_run(body: RunBody, request: Request):
    task, version = _resolve(body)

    if not body.dry_run:
        _ensure_idle(task.id)

    with session() as db:
        run = Run(
            id=new_id("run_"),
            task_id=task.id,
            task_version_id=version.id,
            trigger=body.trigger,
            dry_run=body.dry_run,
            status="queued",
        )
        db.add(run)
        db.commit()

    engine = request.app.state.engine

    if body.wait:
        try:
            return await asyncio.wait_for(engine.execute(run.id), timeout=_WAIT_TIMEOUT_SEC)

        except TimeoutError as e:
            # wait_for has cancelled the run's coroutine; the run closed itself as failed.
            raise ApiError(status_code=504, message=f"Run `{run.id}` did not finish in {_WAIT_TIMEOUT_SEC}s") from e

    _spawn(request, engine.execute(run.id))

    return run_dict(run)


@router.post("/runs/{run_id}/cancel", summary="Cancel a run")
async def cancel_run(run_id: str, request: Request):
    with session() as db:
        run = db.get(Run, run_id)
        if run is None:
            raise ApiError(status_code=404, message=f"Run `{run_id}` not found")

        if run.status in RUN_FINISHED:
            raise ApiError(status_code=409, message=f"Run `{run_id}` is already {run.status}")

    return {"run_id": run_id, "status": await request.app.state.engine.cancel(run_id)}


@router.get("/runs/{run_id}", summary="Run with its steps")
async def get_run(run_id: str):
    run = run_detail(run_id)
    if run is None:
        raise ApiError(status_code=404, message=f"Run `{run_id}` not found")

    return run


def _resolve(body: RunBody) -> tuple[Task, TaskVersion]:
    with session() as db:
        if body.task_version_id:
            version = db.get(TaskVersion, body.task_version_id)
            if version is None:
                raise ApiError(status_code=404, message=f"Version `{body.task_version_id}` not found")

            task = db.get(Task, version.task_id)

            if version.status != "approved" and not body.dry_run:
                raise ApiError(status_code=409, message="Only approved versions can run for real; use dry_run")

            _check_task(task, body)
            return task, version

        if not body.task_id:
            raise ApiError(status_code=400, message="Pass `task_id` or `task_version_id`")

        task = db.get(Task, body.task_id)
        if task is None:
            raise ApiError(status_code=404, message=f"Task `{body.task_id}` not found")

        if not task.current_version_id:
            raise ApiError(status_code=409, message=f"Task `{task.id}` has no approved version yet")

        _check_task(task, body)
        return task, db.get(TaskVersion, task.current_version_id)


def _check_task(task: Task, body: RunBody) -> None:
    """An archived task runs for real no more, however it is asked for; a schedule fires
    only for an active one (a pause the scheduler missed still holds)."""
    if task.status == "archived" and not body.dry_run:
        raise ApiError(status_code=409, message=f"Task `{task.id}` is archived")

    if body.trigger == "cron" and task.status != "active":
        raise ApiError(status_code=409, message=f"Task `{task.id}` is {task.status}: its schedule does not run it")


def _ensure_idle(task_id: str) -> None:
    """One real run per task at a time; the scheduler's next tick simply skips."""
    with session() as db:
        busy = (
            db.query(Run)
            .filter(Run.task_id == task_id, Run.dry_run.is_(False), Run.status.in_(("queued", "running")))
            .first()
        )

    if busy is not None:
        raise ApiError(status_code=409, message=f"Task `{task_id}` already has an unfinished run", details={"run_id": busy.id})


def _spawn(request: Request, coro) -> None:
    tasks: set[asyncio.Task] = request.app.state.background
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
