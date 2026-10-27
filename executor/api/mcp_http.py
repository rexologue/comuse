"""POST /mcp: the executor's MCP server (executor/mcp_server.py) over Streamable HTTP,
the one way to the commands for the chat, the runner and the compiler. Stateless and in
plain JSON: every request stands alone, so nothing is kept between them and nothing
streams.

It is an ordinary route of the executor, so the internal token guards it like the
others; the request itself goes to the SDK's transport as it came."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import APIRouter, FastAPI, Request
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from executor.mcp_server import build_server


router = APIRouter()


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[None]:
    """The transport for the app's lifetime (its registry and runtime are set by then)."""
    manager = StreamableHTTPSessionManager(app=build_server(app.state.registry, app.state.runtime), json_response=True, stateless=True)

    async with manager.run():
        app.state.mcp = manager
        yield


class _Handover(Response):
    """The answer is the transport's: it writes to the connection itself."""

    def __init__(self, manager: StreamableHTTPSessionManager):
        self.manager = manager
        self.background = None


    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


@router.api_route("/mcp", methods=["POST", "GET", "DELETE"], summary="MCP (Streamable HTTP): tools/list, tools/call")
async def mcp_endpoint(request: Request) -> Response:
    return _Handover(request.app.state.mcp)


@router.post("/tools/reload", summary="Rescan internal_tools/ and the own commands' current code")
async def reload_tools(request: Request):
    request.app.state.registry.load()

    return {"tools": len(request.app.state.registry.catalog())}
