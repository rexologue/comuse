from __future__ import annotations

from urllib.parse import unquote

import httpx
from fastapi import Request
from fastapi.responses import StreamingResponse

from common.clients.main_client import INTERNAL_TOKEN_HEADER
from common.errors import ApiError


# Hop-by-hop headers are connection-specific and must not be forwarded (RFC 7230).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


async def reverse_proxy(request: Request, service: str, path: str) -> StreamingResponse:
    """Stream a request to a service and its response back (SSE chat included)."""
    state = request.app.state

    # `..%2F` arrives decoded as `../`, and httpx would resolve it: a public route must
    # never reach another one of the service (or main's own internal routes).
    if any(unquote(part) in (".", "..") for part in path.replace("\\", "/").split("/")):
        raise ApiError(status_code=400, message="Путь с `.` или `..` не принимается")

    target_url = f"{state.registry.url(service)}/{path.lstrip('/')}"
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    body = await request.body()
    headers = _forward_request_headers(request, state.settings.service.internal_token)

    client: httpx.AsyncClient = state.http
    upstream = client.build_request(request.method, target_url, headers=headers, content=body)

    try:
        response = await client.send(upstream, stream=True)

    except httpx.RequestError as e:
        raise ApiError(
            status_code=502,
            message=f"Cannot reach {service} at {target_url}",
            details={"error": repr(e)},
        ) from e

    async def body_stream():
        try:
            async for chunk in response.aiter_raw():
                yield chunk

        finally:
            await response.aclose()

    return StreamingResponse(
        body_stream(),
        status_code=response.status_code,
        headers=_forward_response_headers(response),
    )


def _forward_request_headers(request: Request, internal_token: str) -> dict:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP
        and key.lower() not in ("host", "content-length", "authorization", INTERNAL_TOKEN_HEADER.lower())
    }

    # Services trust main; the client's own credentials stop here.
    if internal_token:
        headers[INTERNAL_TOKEN_HEADER] = internal_token

    return headers


def _forward_response_headers(response: httpx.Response) -> dict:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _HOP_BY_HOP and key.lower() != "content-length"
    }
