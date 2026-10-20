from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.service import create_service_app
from common.storage.db import session
from common.storage.models import Run
from common.utils import utcnow
from runner.api import runs as runs_api
from runner.engine import RunEngine


log = logging.getLogger("runner")


def _close_interrupted_runs() -> None:
    """Runs left unfinished by a restart cannot continue mid-step; they are closed."""
    with session() as db:
        stale = db.query(Run).filter(Run.status.in_(("running", "queued", "waiting"))).all()

        for run in stale:
            run.status = "failed"
            run.error = "Запуск прерван перезапуском сервиса"
            run.finished_at = utcnow()

        db.commit()

    if stale:
        log.warning("marked %d interrupted runs as failed", len(stale))


@asynccontextmanager
async def lifespan(app: FastAPI):

    _close_interrupted_runs()

    app.state.engine = RunEngine(app.state.main)
    app.state.background = set()

    try:
        yield

    finally:
        for task in list(app.state.background):
            task.cancel()

        await asyncio.gather(*app.state.background, return_exceptions=True)


app = create_service_app(
    name="runner",
    title="Runner",
    routers=[(runs_api.router, "runs")],
    lifespan_hook=lifespan,
)
