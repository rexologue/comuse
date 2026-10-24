from __future__ import annotations

from fastapi import APIRouter, Request

from common.errors import ApiError
from common.schemas.execution import ExecuteRequest, ExecuteResponse


router = APIRouter()


@router.post("/execute", summary="Run one tool call", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest, request: Request) -> ExecuteResponse:
    return await request.app.state.runtime.execute(req)


@router.get("/tools", summary="Tool catalog with JSON schemas")
async def list_tools(request: Request):
    return {"tools": request.app.state.registry.catalog()}


@router.get("/tools/{name}", summary="One tool manifest; `version` — an approved version of an external tool")
async def get_tool(name: str, request: Request, version: int | None = None):
    spec = request.app.state.registry.get(name, version)
    if spec is None:
        raise ApiError(status_code=404, message=f"Tool `{name}`{f' v{version}' if version else ''} not found")

    return spec.manifest()


@router.post("/tools/reload", summary="Rescan internal_tools/ and the current external tool versions")
async def reload_tools(request: Request):
    request.app.state.registry.load()

    return {"tools": len(request.app.state.registry.catalog())}
