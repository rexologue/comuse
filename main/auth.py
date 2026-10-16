from __future__ import annotations

import hmac

from fastapi import Request

from common.clients.main_client import INTERNAL_TOKEN_HEADER
from common.errors import ApiError


async def require_client(request: Request) -> None:
    """External API: open when API_TOKEN is empty, otherwise `Authorization: Bearer`.
    Services calling with the internal token are always let through."""
    service = request.app.state.settings.service

    if _valid_internal(request, service.internal_token):
        return

    if not service.api_token:
        return

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(token, service.api_token):
        return

    raise ApiError(status_code=401, message="Missing or invalid API token")


def _valid_internal(request: Request, expected: str) -> bool:
    if not expected:
        return False

    return hmac.compare_digest(request.headers.get(INTERNAL_TOKEN_HEADER, ""), expected)
