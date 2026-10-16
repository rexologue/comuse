from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.service import create_service_app
from scheduler.api import jobs as jobs_api
from scheduler.jobs import TaskScheduler


log = logging.getLogger("scheduler")


@asynccontextmanager
async def lifespan(app: FastAPI):

    scheduler = TaskScheduler(app.state.settings, app.state.main)
    scheduler.start()

    # Jobs live in memory; rebuild them from the active tasks on every start.
    log.info("initial load: %s", scheduler.reload())

    app.state.scheduler = scheduler

    try:
        yield

    finally:
        scheduler.shutdown()


app = create_service_app(
    name="scheduler",
    title="Scheduler",
    routers=[(jobs_api.router, "jobs")],
    lifespan_hook=lifespan,
)
