from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.retention import sweep_forever
from common.service import create_service_app
from main.api import approvals as approvals_api
from main.api import gateway as gateway_api
from main.api import internal as internal_api
from main.api import tasks as tasks_api
from main import events
from main.events import EventBus
from main.orchestrator import Orchestrator
from main.registry import ServiceRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings

    registry = ServiceRegistry(settings.service.internal_token)
    bus = EventBus()

    app.state.registry = registry
    app.state.bus = bus
    app.state.orchestrator = Orchestrator(registry, bus)

    # Long read timeout: a chat turn may run several LLM calls and tools.
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5), trust_env=False)

    sweeper = asyncio.create_task(sweep_forever("events", lambda: events.sweep(settings.storage)))

    try:
        yield

    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        await app.state.http.aclose()
        await registry.close()


# Routers carry their own auth: internal ones need the internal token, the rest
# the optional API token.
app = create_service_app(
    name="main",
    title="Office agent · main",
    routers=[
        (internal_api.router, "internal"),
        (tasks_api.router, "tasks"),
        (approvals_api.router, "approvals"),
        (gateway_api.router, "gateway"),
    ],
    lifespan_hook=lifespan,
    protect=False,
)
