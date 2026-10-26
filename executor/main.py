from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import tunables
from common.errors import ApiError
from common.external_tools import store as external_tools
from common.retention import sweep_forever
from common.secrets import SecretStore
from common.tools.areas import data_areas
from common.service import create_service_app
from executor.api import accounts as accounts_api
from executor.api import execute as execute_api
from executor.api import external as external_api
from executor.outputs import sweep as sweep_outputs
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


log = logging.getLogger("executor")


async def _sweep_tool_versions(storage, registry: ToolRegistry, main) -> int:
    # Only with the owners' word: without it nothing is deleted this time.
    try:
        pins = (await main.get("/internal/tool-pins"))["pins"]

    except ApiError as e:
        log.info("old external tool versions not swept: %s", e.message)
        return 0

    removed = await asyncio.to_thread(external_tools.sweep_unused, storage, {(p["tool"], p["version"]) for p in pins})
    if removed:
        registry.load()

    return removed


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings

    registry = ToolRegistry(settings.storage)
    registry.load()

    secrets = SecretStore.accounts(settings)

    tunables.get("workspace_dir").mkdir(parents=True, exist_ok=True)
    # Every area the tool groups declared (registry.load found them all).
    for area in data_areas():
        (settings.storage.data_dir / area).mkdir(parents=True, exist_ok=True)

    app.state.registry = registry
    app.state.secrets = secrets
    app.state.runtime = ExecutorRuntime(settings, registry, secrets)

    sweepers = [
        asyncio.create_task(sweep_forever("output folders", lambda: sweep_outputs(settings.storage))),
        asyncio.create_task(sweep_forever("external tool versions", lambda: _sweep_tool_versions(settings.storage, registry, app.state.main))),
    ]

    try:
        yield

    finally:
        for sweeper in sweepers:
            sweeper.cancel()
        await asyncio.gather(*sweepers, return_exceptions=True)


app = create_service_app(
    name="executor",
    title="Executor",
    routers=[(execute_api.router, "tools"), (external_api.router, "external tools"), (accounts_api.router, "accounts")],
    lifespan_hook=lifespan,
)
