from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.llm import LLM
from common.service import create_service_app
from compiler.api import tasks as tasks_api
from compiler.compile import Compiler


@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = app.state.settings
    llm = LLM(settings)

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
