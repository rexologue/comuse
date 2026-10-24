"""External API that forwards to agent-loop, compiler and executor. The compiler is
reached only through these routes and main/api/tasks.py: approving or editing a task
must go through the orchestrator (approvals, scheduler reload), never around it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from main.auth import require_client
from main.proxy import METHODS, reverse_proxy


router = APIRouter(dependencies=[Depends(require_client)])


@router.get("/services", summary="Registered services")
async def services(request: Request):
    return request.app.state.registry.status()


@router.post("/compiler/compile", summary="Chat session → draft task + approval")
async def compile_task(body: dict[str, Any], request: Request):
    return await request.app.state.orchestrator.compile(body)


@router.post("/compiler/patch", summary="Instruction → new draft version + diff + approval")
async def patch_task(body: dict[str, Any], request: Request):
    return await request.app.state.orchestrator.patch(body)


@router.api_route("/agent/{path:path}", methods=METHODS, summary="Proxy to agent-loop (chat)")
async def agent_proxy(path: str, request: Request):
    return await reverse_proxy(request, "agent_loop", path)


@router.get("/tools", summary="Tool catalog")
async def tools(request: Request):
    return await reverse_proxy(request, "executor", "tools")


@router.get("/tools/{name}", summary="One tool")
async def tool(name: str, request: Request):
    return await reverse_proxy(request, "executor", f"tools/{name}")


@router.get("/external-tools", summary="External tools with their versions")
async def external_tools(request: Request):
    return await reverse_proxy(request, "executor", "external-tools")


@router.api_route("/external-tools/{path:path}", methods=METHODS, summary="External tools: drafts, versions, approval")
async def external_tool_ops(path: str, request: Request):
    return await reverse_proxy(request, "executor", f"external-tools/{path}")


@router.api_route("/accounts", methods=["GET", "POST"], summary="Accounts (mail and other kinds the tool groups offer)")
async def accounts(request: Request):
    return await reverse_proxy(request, "executor", "accounts")


@router.api_route("/accounts/{path:path}", methods=METHODS, summary="Account operations: kinds, check, delete")
async def account_ops(path: str, request: Request):
    return await reverse_proxy(request, "executor", f"accounts/{path}")


@router.get("/jobs", summary="Scheduled jobs")
async def jobs(request: Request):
    return await reverse_proxy(request, "scheduler", "jobs")
