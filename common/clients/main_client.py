"""Client every service uses to talk to the others — always through main."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError

from common.errors import ApiError
from common.settings import ServiceSettings
from common.tools.mcp_codec import call_meta, manifest_of, response_of
from common.utils import error_body


log = logging.getLogger("main-client")

INTERNAL_TOKEN_HEADER = "X-Internal-Token"

# Calls between services wait as long as the work takes: the executor stops a tool at
# tool_timeout_sec and the LLM client a request at llm_timeout_sec, so a cap here would
# only cut off what the user allowed to run longer.
INTERNAL_TIMEOUT = httpx.Timeout(None, connect=5)


class MainClient:
    def __init__(
        self,
        base_url: str,
        internal_token: str,
        timeout: float | httpx.Timeout = INTERNAL_TIMEOUT,
    ):

        self.base_url = base_url
        self.timeout  = timeout

        headers = {INTERNAL_TOKEN_HEADER: internal_token} if internal_token else {}

        # trust_env=False: service-to-service traffic is local and must bypass proxies.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            trust_env=False,
        )


    @classmethod
    def from_settings(cls, ss: ServiceSettings) -> MainClient:
        return cls(base_url=ss.main_url, internal_token=ss.internal_token)


    async def close(self) -> None:
        await self._client.aclose()


    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Call main and return the decoded JSON; upstream errors keep their status."""
        try:
            response = await self._client.request(method, path, json=json, params=params)

        except httpx.RequestError as e:
            raise ApiError(
                status_code=503,
                message=f"Cannot reach main at {self.base_url}{path}",
                details={"error": repr(e)},
            ) from e

        if response.status_code >= 400:
            body = error_body(response)
            error = body.get("error") if isinstance(body, dict) else None

            if isinstance(error, dict):
                raise ApiError(
                    status_code=response.status_code,
                    message=error.get("message", "Upstream error"),
                    details=error.get("details"),
                )

            raise ApiError(
                status_code=response.status_code,
                message=f"{method} {path} failed with HTTP {response.status_code}",
                details={"body": body},
            )

        if not response.content:
            return None

        return response.json()


    async def get(self, path: str, **kwargs) -> Any:
        return await self.request("GET", path, **kwargs)


    async def post(self, path: str, **kwargs) -> Any:
        return await self.request("POST", path, **kwargs)


    async def put(self, path: str, **kwargs) -> Any:
        return await self.request("PUT", path, **kwargs)


    # -----------------------------
    # TOOLS (the executor's MCP server, through main)
    # -----------------------------

    async def tools(self) -> dict[str, dict[str, Any]]:
        """The commands by name: their manifests, each with its definition's hash."""
        async with self._mcp() as session:
            result = await session.list_tools()

        return {manifest["name"]: manifest for manifest in map(manifest_of, result.tools)}


    async def call_tool(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        hash: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One call, as ExecuteResponse's fields: status, output or error, logs…
        `hash`: the definition the task was approved with (the call is refused if the
        tool is another now)."""
        async with self._mcp() as session:
            result = await session.call_tool(tool, args, meta=call_meta(hash=hash, context=context))

        return response_of(result).model_dump()


    @asynccontextmanager
    async def _mcp(self) -> AsyncIterator[ClientSession]:
        """A session per use: the server keeps none (stateless), so there is nothing to
        hold open between calls."""
        try:
            async with streamable_http_client(f"{self.base_url.rstrip('/')}/executor/mcp", http_client=self._client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

        except BaseException as e:
            converted = _api_error(e)
            if converted is e:
                raise

            raise converted from e


    async def register(self, name: str, url: str) -> None:
        await self.post("/internal/register", json={"name": name, "url": url})


    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Fire-and-forget event; losing one is better than failing the caller."""
        try:
            await self.post("/internal/events", json={"type": event_type, "payload": payload})

        except ApiError as e:
            log.warning("cannot publish event %s: %s", event_type, e.message)


def _api_error(error: BaseException) -> BaseException:
    """What went wrong with an MCP call, as the ApiError any call to main raises. The
    transport runs in a task group, so the cause may come inside an ExceptionGroup."""
    while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
        error = error.exceptions[0]

    if isinstance(error, httpx.HTTPStatusError):
        # The transport streams the answer and has not read it: the status is what there is.
        return ApiError(status_code=error.response.status_code, message=f"Tools (MCP) at main: HTTP {error.response.status_code}")

    if isinstance(error, httpx.RequestError):
        return ApiError(status_code=503, message="Cannot reach main for the tools (MCP)", details={"error": repr(error)})

    if isinstance(error, McpError):
        return ApiError(status_code=502, message=f"MCP: {error.error.message}")

    # Cancellation and anything else go on as they are.
    return error
