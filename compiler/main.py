from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.service import create_service_app
from compiler.api import tasks as tasks_api
from compiler.compile import Compiler


log = logging.getLogger("compiler")


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings
    llm = LLMClient.from_settings(settings.llm)

    try:
        await llm.check_connection([settings.llm.model_chat])
        log.info("llm ok: %s model=%s", settings.llm.base_url, settings.llm.model_chat)

    except ApiError as e:
        log.warning("llm check failed: %s | details=%s", e.message, e.details)

    app.state.compiler = Compiler(settings, llm, app.state.main)

    try:
        yield

    finally:
        await llm.close()


app = create_service_app(
    name="compiler",
    title="Compiler",
    routers=[(tasks_api.router, "tasks")],
    lifespan_hook=lifespan,
)
