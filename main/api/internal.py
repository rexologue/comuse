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


@router.get("/internal/tool-pins", summary="External tool versions tasks and unfinished runs still need")
async def tool_pins(request: Request):
    """Asked by the executor before it deletes old versions: tasks are the compiler's,
    runs the runner's, and each says what it pins."""
    registry = request.app.state.registry
    found: list[dict[str, Any]] = []

    for service in ("compiler", "runner"):
        found += (await registry.client(service).get("/tool-pins"))["pins"]

    return {"pins": found}


@router.api_route("/executor/{path:path}", methods=METHODS, summary="Proxy to executor")
async def executor_proxy(path: str, request: Request):
    return await reverse_proxy(request, "executor", path)


@router.api_route("/runner/{path:path}", methods=METHODS, summary="Proxy to runner")
async def runner_proxy(path: str, request: Request):
    return await reverse_proxy(request, "runner", path)


@router.api_route("/scheduler/{path:path}", methods=METHODS, summary="Proxy to scheduler")
async def scheduler_proxy(path: str, request: Request):
    return await reverse_proxy(request, "scheduler", path)
