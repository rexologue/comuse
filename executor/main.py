from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import tunables
from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.retention import sweep_forever
from common.service import create_service_app
from executor.api import accounts as accounts_api
from executor.api import execute as execute_api
from executor.outputs import sweep as sweep_outputs
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime
from executor.secrets import SecretStore


log = logging.getLogger("executor")


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings

    registry = ToolRegistry()
    registry.load()

    secrets = SecretStore.from_settings(settings.executor, settings.storage)
    llm = LLMClient.from_settings(settings.llm)

    # The LLM is only needed by llm.* tools; a missing server degrades, not blocks.
    try:
        await llm.check_connection([settings.llm.model_instruct])
        log.info("llm ok: %s model=%s", settings.llm.base_url, settings.llm.model_instruct)

    except ApiError as e:
        log.warning("llm check failed: %s | details=%s", e.message, e.details)

    tunables.get("workspace_dir").mkdir(parents=True, exist_ok=True)
    settings.storage.templates_dir.mkdir(parents=True, exist_ok=True)
    settings.storage.outputs_dir.mkdir(parents=True, exist_ok=True)

    app.state.registry = registry
    app.state.secrets = secrets
    app.state.runtime = ExecutorRuntime(settings, registry, secrets, llm)

    sweeper = asyncio.create_task(sweep_forever("output folders", lambda: sweep_outputs(settings.storage)))

    try:
        yield

    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        await llm.close()


app = create_service_app(
    name="executor",
    title="Executor",
    routers=[(execute_api.router, "tools"), (accounts_api.router, "accounts")],
    lifespan_hook=lifespan,
)
