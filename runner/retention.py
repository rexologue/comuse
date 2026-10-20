"""Old runs go: the run, its log and the files it made (data:outputs/<run_id>)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import timedelta

from common.settings import Settings
from common.storage.db import session
from common.storage.models import Run, RunLog
from common.utils import utcnow


log = logging.getLogger("retention")

_SWEEP_EVERY_SEC = 6 * 3600


def sweep(settings: Settings) -> int:
    """Delete finished runs older than RUN_RETENTION_DAYS; returns how many went."""
    cutoff = utcnow() - timedelta(days=settings.runner.retention_days)

    with session() as db:
        old = [run_id for (run_id,) in db.query(Run.id).filter(Run.finished_at.is_not(None), Run.finished_at < cutoff)]

        if old:
            db.query(RunLog).filter(RunLog.run_id.in_(old)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(old)).delete(synchronize_session=False)
            db.commit()

    for run_id in old:
        shutil.rmtree(settings.storage.outputs_dir / run_id, ignore_errors=True)

    return len(old)


async def sweep_forever(settings: Settings) -> None:
    while True:
        try:
            removed = sweep(settings)
            if removed:
                log.info("removed %d runs older than %d days", removed, settings.runner.retention_days)

        except Exception:
            log.exception("retention sweep failed")

        await asyncio.sleep(_SWEEP_EVERY_SEC)
