from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from common.clients.main_client import INTERNAL_TIMEOUT
from common.retention import sweep_forever
from common.service import create_service_app
from main.api import approvals as approvals_api
from main.api import gateway as gateway_api
from main.api import internal as internal_api
from main.api import llm as llm_api
from main.api import logs as logs_api
from main.api import settings as settings_api
from main.api import tasks as tasks_api
from main import events, providers
from main.auth import Guard, api_token
from main.events import EventBus
from main.orchestrator import Orchestrator
from main.registry import ServiceRegistry


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings

    app.state.api_token = api_token(settings)

    # Models named by LLM_* variables become providers (once; later they are ordinary).
    providers.register_env_models(settings)

    registry = ServiceRegistry(settings.service.internal_token)
    bus = EventBus()

    app.state.registry = registry
    app.state.bus = bus
    app.state.orchestrator = Orchestrator(registry, bus)

    # No read cap: a chat turn may run several LLM calls and tools (see INTERNAL_TIMEOUT).
    app.state.http = httpx.AsyncClient(timeout=INTERNAL_TIMEOUT, trust_env=False)

    sweeper = asyncio.create_task(sweep_forever("events", events.sweep))

    try:
        yield

    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        await app.state.http.aclose()
        await registry.close()


# Routers carry their own auth: internal ones need the internal token, the rest
# the API token.
app = create_service_app(
    name="main",
    title="Office agent · main",
    routers=[
        (internal_api.router, "internal"),
        (tasks_api.router, "tasks"),
        (approvals_api.router, "approvals"),
        (settings_api.router, "settings"),
        (llm_api.router, "llm"),
        (logs_api.router, "logs"),
        (gateway_api.router, "gateway"),
    ],
    lifespan_hook=lifespan,
    protect=False,
)

# Host check and CORS before any route (main/auth.py).
app.add_middleware(Guard)
