"""Old runs go with their log; their files under data:outputs are the executor's to sweep."""

from __future__ import annotations

from common.retention import cutoff
from common.storage.db import session
from common.storage.models import Run, RunLog


def sweep() -> int:
    """Delete finished runs older than `retention_days`; returns how many went."""
    with session() as db:
        old = [run_id for (run_id,) in db.query(Run.id).filter(Run.finished_at.is_not(None), Run.finished_at < cutoff())]

        if old:
            db.query(RunLog).filter(RunLog.run_id.in_(old)).delete(synchronize_session=False)
            db.query(Run).filter(Run.id.in_(old)).delete(synchronize_session=False)
            db.commit()

    return len(old)
