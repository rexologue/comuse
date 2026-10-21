from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_loop.api import chat as chat_api
from agent_loop.catalog import ToolCatalog
from agent_loop.loop import AgentLoop
from common.llm import LLM
from common.service import create_service_app


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings
    llm = LLM(settings)

    catalog = ToolCatalog(app.state.main, llm.embed)
    app.state.loop = AgentLoop(settings, llm, app.state.main, catalog)

    try:
        yield

    finally:
        await llm.close()


app = create_service_app(
    name="agent_loop",
    title="Agent loop",
    routers=[(chat_api.router, "chat")],
    lifespan_hook=lifespan,
)
