from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from common.errors import ApiError
from common.storage.queries import run_detail, run_log
from main import views
from main.auth import require_client
from main.proxy import reverse_proxy


router = APIRouter(dependencies=[Depends(require_client)])


class RunBody(BaseModel):
    task_id: str | None = None
    task_version_id: str | None = None
    dry_run: bool = False
    wait: bool = False


# -----------------------------
# TASKS
# -----------------------------

@router.get("/tasks", summary="Tasks with their active scenario cards; `tool` — only those using it")
async def list_tasks(tool: str | None = None):
    return {"tasks": views.task_list(tool=tool)}


@router.get("/tasks/{task_id}", summary="Task, current version and history")
async def get_task(task_id: str, request: Request):
    task = views.task_detail(task_id, await _catalog(request))
    if task is None:
        raise ApiError(status_code=404, message=f"Task `{task_id}` not found")

    return task


@router.get("/tasks/{task_id}/versions/{version_id}", summary="One version")
async def get_version(task_id: str, version_id: str):
    version = views.version_detail(task_id, version_id)
    if version is None:
        raise ApiError(status_code=404, message=f"Version `{version_id}` not found")

    return version


@router.get("/tasks/{task_id}/versions/{version_id}/diff", summary="Diff against the parent version")
async def get_diff(task_id: str, version_id: str, request: Request):
    return await reverse_proxy(request, "compiler", f"tasks/{task_id}/versions/{version_id}/diff")


@router.post("/tasks/{task_id}/versions", summary="Edit a task by hand (spec or JSON Patch ops) → draft version + diff + approval")
async def edit_task(task_id: str, body: dict[str, Any], request: Request):
    return await request.app.state.orchestrator.edit(task_id, body)


@router.post("/tasks/{task_id}/tools/refresh", summary="The same steps with the tools as they are now → draft version + diff + approval")
async def refresh_tools(task_id: str, request: Request):
    return await request.app.state.orchestrator.refresh_tools(task_id)


@router.post("/tasks/{task_id}/versions/{version_id}/approve", summary="Make a version active")
async def approve(task_id: str, version_id: str, request: Request):
    return await request.app.state.orchestrator.approve_version(task_id, version_id)


@router.post("/tasks/{task_id}/versions/{version_id}/reject", summary="Reject a draft version")
async def reject(task_id: str, version_id: str, request: Request):
    return await request.app.state.orchestrator.reject_version(task_id, version_id)


@router.api_route("/tasks/{task_id}/settings", methods=["GET", "PATCH"], summary="Task settings: GET default / whole task / per step, PATCH {changes: [{tool, field, value, step?}]}")
async def task_settings(task_id: str, request: Request):
    return await reverse_proxy(request, "compiler", f"tasks/{task_id}/settings")


@router.post("/tasks/{task_id}/{action}", summary="pause | resume | archive")
async def task_action(task_id: str, action: str, request: Request):
    # Which actions there are is the compiler's: an unknown one comes back 404 from it.
    return await request.app.state.orchestrator.set_task_status(task_id, action)


@router.get("/tasks/{task_id}/runs", summary="Run history of a task (?include_empty=true adds checks that found nothing)")
async def task_runs(task_id: str, include_empty: bool = False):
    return {"runs": views.run_list(task_id, include_empty=include_empty)}


# -----------------------------
# RUNS
# -----------------------------

@router.post("/runs", summary="Run a task now, or dry-run a version (task_version_id + dry_run)")
async def start_run(body: RunBody, request: Request) -> Any:
    payload = {**body.model_dump(exclude_none=True), "trigger": "dry_run" if body.dry_run else "manual"}

    return await request.app.state.registry.client("runner").post("/runs", json=payload)


@router.get("/runs/{run_id}", summary="Run status with its steps")
async def get_run(run_id: str):
    run = run_detail(run_id)
    if run is None:
        raise ApiError(status_code=404, message=f"Run `{run_id}` not found")

    return run


@router.get("/runs/{run_id}/log", summary="The run's whole log: start, every step, notifications, end")
async def get_run_log(run_id: str):
    entries = run_log(run_id)
    if entries is None:
        raise ApiError(status_code=404, message=f"Run `{run_id}` not found")

    return {"run_id": run_id, "log": entries}


@router.post("/runs/{run_id}/cancel", summary="Cancel a run")
async def cancel_run(run_id: str, request: Request):
    return await request.app.state.registry.client("runner").post(f"/runs/{run_id}/cancel")


async def _catalog(request: Request) -> dict[str, dict[str, Any]] | None:
    """The tools as they are now: the executor keeps them. Without its answer the task is
    shown without what changed in its tools."""
    try:
        return await request.app.state.main.tools()

    except ApiError:
        return None
