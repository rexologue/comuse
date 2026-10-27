from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common import tunables
from common.retention import sweep_forever
from common.secrets import SecretStore
from common.tools.areas import data_areas
from common.service import create_service_app
from executor.api import accounts as accounts_api
from executor.api import mcp_http as mcp_api
from executor.api import plugins as plugins_api
from executor.outputs import sweep as sweep_outputs
from executor.plugins.launch import Plugins, first_account
from executor.registry import ToolRegistry
from executor.runtime import ExecutorRuntime


log = logging.getLogger("executor")


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

    # Before serving: a run that came first would find a plugin's tools missing.
    app.state.plugins = Plugins(registry, settings.storage, first_account(app.state.runtime.load_credential))
    await app.state.plugins.start_all()

    sweepers = [
        asyncio.create_task(sweep_forever("output folders", lambda: sweep_outputs(settings.storage))),
    ]

    try:
        async with mcp_api.serving(app):
            yield

    finally:
        for sweeper in sweepers:
            sweeper.cancel()
        await asyncio.gather(*sweepers, return_exceptions=True)
        await registry.close()


app = create_service_app(
    name="executor",
    title="Executor",
    routers=[(mcp_api.router, "tools"), (plugins_api.router, "plugins"), (accounts_api.router, "accounts")],
    lifespan_hook=lifespan,
)
