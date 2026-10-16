"""Read helpers shared by services that show runs (main and runner)."""

from __future__ import annotations

from typing import Any

from common.storage.db import session
from common.storage.models import Run, StepRun


def run_detail(run_id: str) -> dict[str, Any] | None:
    with session() as db:
        run = db.get(Run, run_id)
        if run is None:
            return None

        steps = db.query(StepRun).filter(StepRun.run_id == run_id).order_by(StepRun.id).all()

    return {
        **run_dict(run),
        "state": run.state,
        "steps": [
            {
                "step_key": s.step_key,
                "tool": s.tool,
                "attempt": s.attempt,
                "status": s.status,
                "input": s.input,
                "output": s.output,
                "error": s.error,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            }
            for s in steps
        ],
    }


def run_dict(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "task_version_id": run.task_version_id,
        "trigger": run.trigger,
        "status": run.status,
        "dry_run": run.dry_run,
        "result": run.result,
        "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
