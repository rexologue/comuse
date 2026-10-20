"""Read helpers shared by services that show runs (main and runner)."""

from __future__ import annotations

from typing import Any

from common.storage.db import session
from common.storage.models import Run, RunLog


def run_detail(run_id: str) -> dict[str, Any] | None:
    """The run with its step entries from the log (the full log is `run_log`)."""
    with session() as db:
        run = db.get(Run, run_id)
        if run is None:
            return None

        steps = db.query(RunLog).filter(RunLog.run_id == run_id, RunLog.kind == "step").order_by(RunLog.seq).all()

    return {**run_dict(run), "steps": [{"step_key": s.step_key, "at": s.at.isoformat(), **s.data} for s in steps]}


def run_log(run_id: str) -> list[dict[str, Any]] | None:
    with session() as db:
        if db.get(Run, run_id) is None:
            return None

        entries = db.query(RunLog).filter(RunLog.run_id == run_id).order_by(RunLog.seq).all()

    return [
        {"seq": e.seq, "at": e.at.isoformat(), "kind": e.kind, "step_key": e.step_key, **e.data}
        for e in entries
    ]


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
