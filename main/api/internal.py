"""Routes only services call: the internal token (the API token when none is configured)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from main.auth import require_service
from main.proxy import METHODS, reverse_proxy


router = APIRouter(dependencies=[Depends(require_service)])


class RegisterBody(BaseModel):
    name: str
    url: str


class EventBody(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/internal/register", summary="A service announces its address")
async def register(body: RegisterBody, request: Request):
    request.app.state.registry.register(body.name, body.url)

    return {"status": "ok"}


class NotifyBody(BaseModel):
    task_id: str
    run_id: str
    title: str
    message: str


@router.post("/internal/notify", summary="A task run tells its user something")
async def notify(body: NotifyBody, request: Request):
    return await request.app.state.orchestrator.notify(**body.model_dump())


@router.post("/internal/events", summary="Publish an event")
async def publish(body: EventBody, request: Request):
    return request.app.state.bus.publish(body.type, body.payload)


class ToolsChangedBody(BaseModel):
    task_id: str
    run_id: str
    title: str
    tools: list[str]
    # Those of `tools` the catalog has no more.
    gone: list[str] = []
    dry_run: bool = False


@router.post("/internal/tools-changed", summary="A run was refused: tools of its task are not the ones it was approved with")
async def tools_changed(body: ToolsChangedBody, request: Request):
    return await request.app.state.orchestrator.tools_changed(**body.model_dump())


@router.api_route("/executor/{path:path}", methods=METHODS, summary="Proxy to executor")
async def executor_proxy(path: str, request: Request):
    return await reverse_proxy(request, "executor", path)


@router.api_route("/runner/{path:path}", methods=METHODS, summary="Proxy to runner")
async def runner_proxy(path: str, request: Request):
    return await reverse_proxy(request, "runner", path)


@router.api_route("/scheduler/{path:path}", methods=METHODS, summary="Proxy to scheduler")
async def scheduler_proxy(path: str, request: Request):
    return await reverse_proxy(request, "scheduler", path)
