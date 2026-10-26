from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from common.errors import ApiError
from common.schemas.task import TaskSpec
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


class ToolVersionBody(BaseModel):
    tool: str
    # None: the tool's current approved version.
    version: int | None = None


@router.get("/tool-pins", summary="External tool versions the tasks may still run")
async def tool_pins():
    return {"pins": store.tool_pins()}


@router.post("/compile", summary="Chat session → draft task (nothing runs)")
async def compile_task(body: CompileBody, request: Request):
    return await request.app.state.compiler.compile(body.session_id, title=body.title, hint=body.hint)


@router.post("/patch", summary="Instruction → new draft version and diff")
async def patch_task(body: PatchBody, request: Request):
    return await request.app.state.compiler.patch(body.task_id, body.instruction, session_id=body.session_id)


class EditBody(BaseModel):
    # Exactly one: the whole new spec, or a JSON Patch (RFC 6902) to the current one.
    spec: dict[str, Any] | None = None
    ops: list[dict[str, Any]] | None = None
    summary: str | None = None


@router.post("/tasks/{task_id}/versions", summary="The user's own edit, no LLM → new draft version and diff")
async def edit_task(task_id: str, body: EditBody, request: Request):
    return await request.app.state.compiler.edit(task_id, spec=body.spec, ops=body.ops, summary=body.summary)


@router.post("/tasks/{task_id}/tool-versions", summary="Move the task's steps to another version of an external tool → draft version")
async def move_tool_version(task_id: str, body: ToolVersionBody, request: Request):
    return await request.app.state.compiler.move_to_tool_version(task_id, body.tool, body.version)


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


class SettingsChanges(BaseModel):
    # Each {tool, field, value, step?}: no step — for the whole task; value null — back.
    changes: list[dict[str, Any]]


@router.get("/tasks/{task_id}/settings", summary="The settings of the task's tools: default, for the whole task, per step")
async def get_settings(task_id: str, request: Request):
    return await _settings_view(store.get_task(task_id), request)


@router.patch("/tasks/{task_id}/settings", summary="Change settings: [{tool, field, value, step?}], value null takes it back")
async def patch_settings(task_id: str, body: SettingsChanges, request: Request):
    task = store.get_task(task_id)
    version = _version_or_404(task)
    catalog = await request.app.state.compiler.catalog()

    settings = task_settings.apply(task.settings or {}, body.changes, TaskSpec.model_validate(version.spec), catalog)

    return await _settings_view(store.set_settings(task_id, settings), request)


async def _settings_view(task, request: Request) -> dict[str, Any]:
    version = _version_or_404(task)
    catalog = await request.app.state.compiler.catalog()
    spec = TaskSpec.model_validate(version.spec)

    return {"task_id": task.id, "settings": task_settings.view(task.settings or {}, spec, catalog, version.cards or [])}


def _version_or_404(task):
    version = store.shown_version(task)
    if version is None:
        raise ApiError(status_code=404, message=f"Task `{task.id}` has no version")

    return version


@router.post("/tasks/{task_id}/{action}", summary="pause | resume | archive")
async def set_status(task_id: str, action: str):
    task = store.set_status(task_id, action)

    return {"task_id": task.id, "status": task.status}
