"""Routes only services call (internal token required when configured)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from common.service import require_internal_token
from main.proxy import METHODS, reverse_proxy


router = APIRouter(dependencies=[Depends(require_internal_token)])


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


@router.post("/internal/events", summary="Publish an event")
async def publish(body: EventBody, request: Request):
    return request.app.state.bus.publish(body.type, body.payload)


@router.api_route("/executor/{path:path}", methods=METHODS, summary="Proxy to executor")
async def executor_proxy(path: str, request: Request):
    return await reverse_proxy(request, "executor", path)


@router.api_route("/runner/{path:path}", methods=METHODS, summary="Proxy to runner")
async def runner_proxy(path: str, request: Request):
    return await reverse_proxy(request, "runner", path)


@router.api_route("/scheduler/{path:path}", methods=METHODS, summary="Proxy to scheduler")
async def scheduler_proxy(path: str, request: Request):
    return await reverse_proxy(request, "scheduler", path)
