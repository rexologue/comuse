from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_loop import history
from agent_loop.api import chat as chat_api
from agent_loop.catalog import ToolCatalog
from agent_loop.loop import AgentLoop
from common import tunables
from common.llm import LLM
from common.retention import sweep_forever
from common.service import create_service_app


# Temporary chats are checked hourly: their limit is hours, not days.
_CHAT_SWEEP_EVERY_SEC = 3600


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings
    llm = LLM(settings)

    catalog = ToolCatalog(app.state.main, llm.embed)
    loop = AgentLoop(settings, llm, app.state.main, catalog)
    app.state.loop = loop

    def sweep_chats() -> int:
        gone = history.sweep_idle(tunables.get("chat_idle_hours"))
        loop.forget(gone)
        return len(gone)

    sweeper = asyncio.create_task(sweep_forever("temporary chats", sweep_chats, every_sec=_CHAT_SWEEP_EVERY_SEC))

    try:
        yield

    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        await llm.close()


app = create_service_app(
    name="agent_loop",
    title="Agent loop",
    routers=[(chat_api.router, "chat")],
    lifespan_hook=lifespan,
)
