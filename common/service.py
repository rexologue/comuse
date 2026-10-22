"""Common FastAPI scaffolding: logging, storage, main client, registration, auth."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, Request

from common import tunables
from common.clients.main_client import INTERNAL_TOKEN_HEADER, MainClient
from common.errors import ApiError, install_error_handlers
from common.settings import Settings
from common.storage import migrate
from common.storage.db import init_engine


log = logging.getLogger("service")

# Services re-register periodically so a restarted main relearns their addresses soon.
_REGISTER_INTERVAL_SEC = 10
_FIRST_REGISTER_RETRY_SEC = 0.25

# How long a service waits for main to bring the database schema up to date.
_SCHEMA_WAIT_SEC = 60

LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def configure_logging() -> None:
    """Send structured logs to stdout at the LOG_LEVEL from the environment."""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # httpx logs every request at INFO: each call between services and to the LLM.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def require_internal_token(request: Request) -> None:
    """Only main (and other services via main) may call a service directly."""
    expected = request.app.state.settings.service.internal_token
    if not expected:
        return

    provided = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    if not hmac.compare_digest(provided, expected):
        raise ApiError(status_code=401, message="Missing or invalid internal token")


ops_router = APIRouter()


@ops_router.get("/health", summary="Liveness")
async def health(request: Request):
    return {"status": "ok", "service": request.app.state.settings.service.name}


async def _register_forever(main: MainClient, name: str, url: str) -> None:
    registered = False

    while True:
        try:
            await main.register(name, url)
            registered = True

        except ApiError as e:
            # Before the first success main may simply not be up yet (in one process it
            # starts serving only after every service has started): ask again soon.
            log.log(logging.WARNING if registered else logging.DEBUG, "registration in main failed: %s", e.message)

        await asyncio.sleep(_REGISTER_INTERVAL_SEC if registered else _FIRST_REGISTER_RETRY_SEC)


def create_service_app(
    *,
    name: str,
    title: str,
    routers: list[tuple[APIRouter, str]],
    lifespan_hook: LifespanHook | None = None,
    protect: bool = True,
) -> FastAPI:
    """Build a service app. `routers` are (router, tag) pairs; with `protect` every
    route except /health requires the internal token."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):

        configure_logging()

        settings = Settings.from_env(name)
        tunables.check_env()
        engine = init_engine(settings.storage)

        # main owns the schema: it migrates before serving, the others wait for it.
        if name == "main":
            migrate.upgrade(engine, settings.storage)
        else:
            await asyncio.to_thread(migrate.wait_current, engine, _SCHEMA_WAIT_SEC)

        main = MainClient.from_settings(settings.service)

        app.state.settings = settings
        app.state.main = main

        register_task = None
        if name != "main":
            register_task = asyncio.create_task(
                _register_forever(main, name, settings.service.self_url)
            )

        log.info(
            "ready: service=%s self_url=%s main=%s data_dir=%s",
            name,
            settings.service.self_url,
            settings.service.main_url,
            settings.storage.data_dir,
        )

        try:
            async with (lifespan_hook(app) if lifespan_hook else nullcontext()):
                yield

        finally:
            if register_task is not None:
                register_task.cancel()
                try:
                    await register_task
                except asyncio.CancelledError:
                    pass

            await main.close()

    app = FastAPI(title=title, version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(ops_router, tags=["ops"])

    dependencies = [Depends(require_internal_token)] if protect else []
    for router, tag in routers:
        app.include_router(router, tags=[tag], dependencies=dependencies)

    return app
