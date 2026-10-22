"""All six services in one process and on one port (RUN_MODE=single, the launcher's
default). main is at the root; every other service is mounted under /svc/<name> and
registers that address, so they still reach each other only through main, over HTTP,
just on the same port. One Python instead of six: the libraries load once.

The services start in order, main first (it migrates the database), and stop in
reverse. The cost of one process: a service that blocks the event loop or crashes
takes the others with it (the launcher restarts the process)."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from agent_loop.main import app as agent_loop
from common.settings import SINGLE_PREFIX
from compiler.main import app as compiler
from executor.main import app as executor
from main.main import app as main
from runner.main import app as runner
from scheduler.main import app as scheduler


# The launcher's start order after main.
SERVICES = {"executor": executor, "agent_loop": agent_loop, "compiler": compiler, "runner": runner, "scheduler": scheduler}


@asynccontextmanager
async def lifespan(_app):
    # A mounted app's lifespan does not run by itself.
    async with AsyncExitStack() as stack:
        for service in (main, *SERVICES.values()):
            await stack.enter_async_context(service.router.lifespan_context(service))

        yield


app = Starlette(
    routes=[*(Mount(f"{SINGLE_PREFIX}/{name}", service) for name, service in SERVICES.items()), Mount("", main)],
    lifespan=lifespan,
)
