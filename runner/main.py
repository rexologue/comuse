from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import func

from common.service import create_service_app
from common.storage.db import session
from common.storage.models import Run, RunLog
from common.utils import utcnow
from runner.api import runs as runs_api
from runner.engine import RunEngine
from common.retention import sweep_forever
from runner.retention import sweep


log = logging.getLogger("runner")


def _close_interrupted_runs() -> None:
    """Runs left unfinished by a restart cannot continue mid-step; they are closed, with
    the end in their log like any other run."""
    error = "Запуск прерван перезапуском сервиса"

    with session() as db:
        stale = db.query(Run).filter(Run.status.in_(("running", "queued"))).all()

        for run in stale:
            run.status = "failed"
            run.error = error
            run.finished_at = utcnow()

            last = db.query(func.max(RunLog.seq)).filter(RunLog.run_id == run.id).scalar() or 0
            db.add(RunLog(run_id=run.id, task_id=run.task_id, seq=last + 1, kind="end", data={"status": "failed", "error": error, "result": run.result}))

        db.commit()

    if stale:
        log.warning("marked %d interrupted runs as failed", len(stale))


@asynccontextmanager
async def lifespan(app: FastAPI):

    _close_interrupted_runs()

    app.state.engine = RunEngine(app.state.main)
    app.state.background = {asyncio.create_task(sweep_forever("runs", sweep))}

    try:
        yield

    finally:
        app.state.engine.stopping = True

        for task in list(app.state.background):
            task.cancel()

        await asyncio.gather(*app.state.background, return_exceptions=True)


app = create_service_app(
    name="runner",
    title="Runner",
    routers=[(runs_api.router, "runs")],
    lifespan_hook=lifespan,
)
