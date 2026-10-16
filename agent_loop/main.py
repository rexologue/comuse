from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_loop.api import chat as chat_api
from agent_loop.catalog import ToolCatalog
from agent_loop.loop import AgentLoop
from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.service import create_service_app


log = logging.getLogger("agent-loop")


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings
    llm = LLMClient.from_settings(settings.llm)

    try:
        await llm.check_connection([settings.llm.model_agent, settings.llm.model_embed])
        log.info("llm ok: %s agent=%s embed=%s", settings.llm.base_url, settings.llm.model_agent, settings.llm.model_embed)

    except ApiError as e:
        log.warning("llm check failed: %s | details=%s", e.message, e.details)

    catalog = ToolCatalog(app.state.main, llm, settings.llm.model_embed, settings.agent.tools_top_k)
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
