from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from common.errors import ApiError
from compiler import store, task_settings
from compiler.validate import SpecError, validate_spec


router = APIRouter()


class CompileBody(BaseModel):
    session_id: str
    title: str | None = None
    hint: str | None = None


class PatchBody(BaseModel):
    task_id: str
    instruction: str
    session_id: str | None = None


@router.post("/compile", summary="Chat session → draft task (with dry run)")
async def compile_task(body: CompileBody, request: Request):
    return await request.app.state.compiler.compile(body.session_id, title=body.title, hint=body.hint)


@router.post("/patch", summary="Instruction → new draft version and diff")
async def patch_task(body: PatchBody, request: Request):
    return await request.app.state.compiler.patch(body.task_id, body.instruction)


@router.post("/validate", summary="Check a spec against the tool catalog")
async def validate(spec: dict[str, Any], request: Request):
    catalog = await request.app.state.compiler.catalog()

    try:
        validate_spec(spec, catalog)

    except SpecError as e:
        return {"valid": False, "errors": e.errors}

    return {"valid": True, "errors": []}


@router.get("/tasks/{task_id}/versions/{version_id}/diff", summary="Diff against the parent version")
async def diff(task_id: str, version_id: str, request: Request):
    return await request.app.state.compiler.diff(task_id, version_id)


@router.post("/tasks/{task_id}/versions/{version_id}/approve", summary="Activate a draft version")
async def approve(task_id: str, version_id: str, request: Request):
    task = store.approve(task_id, version_id, await request.app.state.compiler.catalog())

    return {"task_id": task.id, "status": task.status, "current_version_id": task.current_version_id}


@router.post("/tasks/{task_id}/versions/{version_id}/reject", summary="Reject a draft version")
async def reject(task_id: str, version_id: str):
    version = store.reject(task_id, version_id)

    return {"task_id": task_id, "version_id": version.id, "status": version.status}


@router.get("/tasks/{task_id}/settings", summary="The task's domain settings with titles and descriptions")
async def get_settings(task_id: str, request: Request):
    task = store.get_task(task_id)

    return {"task_id": task.id, "settings": task_settings.describe(task.settings or {}, await request.app.state.compiler.catalog())}


@router.put("/tasks/{task_id}/settings", summary="Change values (not keys): {domain: {field: value}}")
async def put_settings(task_id: str, changes: dict[str, Any], request: Request):
    catalog = await request.app.state.compiler.catalog()
    task = store.get_task(task_id)
    task = store.set_settings(task_id, task_settings.update(task.settings or {}, changes, catalog))

    return {"task_id": task.id, "settings": task_settings.describe(task.settings, catalog)}


@router.post("/tasks/{task_id}/{action}", summary="pause | resume | archive")
async def set_status(task_id: str, action: str):
    if action not in ("pause", "resume", "archive"):
        raise ApiError(status_code=404, message=f"Unknown action `{action}`")

    task = store.set_status(task_id, action)

    return {"task_id": task.id, "status": task.status}
