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
    embedder = LLMClient.embeddings_from_settings(settings.llm)

    try:
        await llm.check_connection([settings.llm.model_chat])
        log.info("llm ok: %s chat=%s", settings.llm.base_url, settings.llm.model_chat)

    except ApiError as e:
        log.warning("llm check failed: %s | details=%s", e.message, e.details)

    catalog = ToolCatalog(app.state.main, embedder, settings.llm.model_embed, settings.agent.tools_top_k)

    if settings.llm.model_embed:
        try:
            await embedder.check_connection([settings.llm.model_embed])
            log.info("embeddings ok: %s model=%s", settings.llm.embed_base_url, settings.llm.model_embed)

        except ApiError as e:
            log.warning("embeddings check failed (all tools will be shown): %s | details=%s", e.message, e.details)
            catalog.mark_embeddings_down()
    app.state.loop = AgentLoop(settings, llm, app.state.main, catalog)

    try:
        yield

    finally:
        await llm.close()
        await embedder.close()


app = create_service_app(
    name="agent_loop",
    title="Agent loop",
    routers=[(chat_api.router, "chat")],
    lifespan_hook=lifespan,
)
