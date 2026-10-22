"""Who may call main from outside, and from where.

- **The API token is always required.** API_TOKEN, or else a token main creates on its
  first start and keeps in DATA_DIR/api_token (readable by the user only): a client on
  the same computer reads it there. Any web page the user opens can send requests to
  127.0.0.1; without the token they are refused.
- **Host check** (ALLOWED_HOSTS): a request whose Host header names anything else is
  refused, so a page that rebinds its own domain to 127.0.0.1 (DNS rebinding) gets
  nothing even before the token.
- **CORS** (ALLOWED_ORIGINS): off unless origins are listed; a browser frontend on
  another origin needs its own there."""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

from fastapi import Request
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from common.clients.main_client import INTERNAL_TOKEN_HEADER
from common.errors import ApiError
from common.settings import Settings


log = logging.getLogger("auth")


def api_token(settings: Settings) -> str:
    """API_TOKEN, or the one kept in DATA_DIR/api_token (made on the first start)."""
    if settings.service.api_token:
        return settings.service.api_token

    path = token_path(settings)

    if path.is_file() and (token := path.read_text(encoding="utf-8").strip()):
        return token

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Created readable by the user only (on Windows the profile folder does that).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(token)

    log.info("API token created: %s", path)

    return token


def token_path(settings: Settings) -> Path:
    return settings.storage.data_dir / "api_token"


async def require_client(request: Request) -> None:
    """External API: `Authorization: Bearer <token>`. Services calling with the internal
    token are let through."""
    if _valid_internal(request, request.app.state.settings.service.internal_token):
        return

    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(token, request.app.state.api_token):
        return

    raise ApiError(status_code=401, message="Missing or invalid API token")


def _valid_internal(request: Request, expected: str) -> bool:
    if not expected:
        return False

    return hmac.compare_digest(request.headers.get(INTERNAL_TOKEN_HEADER, ""), expected)


class Guard:
    """Host check and CORS around main. Settings load in the lifespan, so the checks are
    built on the first request rather than at import."""

    def __init__(self, app: ASGIApp):

        self.app = app
        self._guarded: ASGIApp | None = None


    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if self._guarded is None:
            self._guarded = _guarded(self.app, scope["app"].state.settings)

        await self._guarded(scope, receive, send)


def _guarded(app: ASGIApp, settings: Settings) -> ASGIApp:
    service = settings.service

    if service.allowed_origins:
        app = CORSMiddleware(
            app,
            allow_origins=service.allowed_origins,
            allow_methods=["*"],
            allow_headers=["Authorization", "Content-Type"],
        )

    # The outermost check: a foreign Host gets 400 before anything else runs.
    if "*" not in service.allowed_hosts:
        app = TrustedHostMiddleware(app, allowed_hosts=service.allowed_hosts)

    return app
