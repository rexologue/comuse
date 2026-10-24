from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import tunables
from common.llm import LLM
from common.external_tools import store as external_tools
from common.retention import sweep_forever
from common.secrets import SecretStore
from common.service import create_service_app
from executor.api import accounts as accounts_api
from executor.api import execute as execute_api
from executor.api import external as external_api
from executor.outputs import sweep as sweep_outputs
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime



def _sweep_tool_versions(storage, registry: ToolRegistry) -> int:
    removed = external_tools.sweep_unused(storage)
    if removed:
        registry.load()

    return removed


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings

    registry = ToolRegistry(settings.storage)
    registry.load()

    secrets = SecretStore.accounts(settings)
    # Only llm.* tools need the LLM; without one the rest still work.
    llm = LLM(settings)

    tunables.get("workspace_dir").mkdir(parents=True, exist_ok=True)
    settings.storage.templates_dir.mkdir(parents=True, exist_ok=True)
    settings.storage.outputs_dir.mkdir(parents=True, exist_ok=True)

    app.state.registry = registry
    app.state.secrets = secrets
    app.state.runtime = ExecutorRuntime(settings, registry, secrets, llm)

    sweepers = [
        asyncio.create_task(sweep_forever("output folders", lambda: sweep_outputs(settings.storage))),
        asyncio.create_task(sweep_forever("external tool versions", lambda: _sweep_tool_versions(settings.storage, registry))),
    ]

    try:
        yield

    finally:
        for sweeper in sweepers:
            sweeper.cancel()
        await asyncio.gather(*sweepers, return_exceptions=True)
        await llm.close()


app = create_service_app(
    name="executor",
    title="Executor",
    routers=[(execute_api.router, "tools"), (external_api.router, "external tools"), (accounts_api.router, "accounts")],
    lifespan_hook=lifespan,
)
