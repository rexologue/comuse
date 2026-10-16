"""Read-only views over the shared tables (main never writes tasks or runs)."""

from __future__ import annotations

from typing import Any

from common.storage.db import session
from common.storage.models import Run, StepRun, Task, TaskVersion


def task_list() -> list[dict[str, Any]]:
    with session() as db:
        tasks = db.query(Task).filter(Task.status != "archived").order_by(Task.created_at.desc()).all()
        versions = {v.id: v for v in db.query(TaskVersion).filter(TaskVersion.task_id.in_([t.id for t in tasks]))}

    return [_task_summary(t, versions.get(t.current_version_id)) for t in tasks]


def task_detail(task_id: str) -> dict[str, Any] | None:
    with session() as db:
        task = db.get(Task, task_id)
        if task is None:
            return None

        versions = (
            db.query(TaskVersion)
            .filter(TaskVersion.task_id == task_id)
            .order_by(TaskVersion.version_no.desc())
            .all()
        )

    current = next((v for v in versions if v.id == task.current_version_id), None)

    return {
        **_task_summary(task, current),
        "current_version": version_dict(current) if current else None,
        "versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "status": v.status,
                "created_by": v.created_by,
                "summary": v.summary,
                "created_at": v.created_at.isoformat(),
            }
            for v in versions
        ],
    }


def version_detail(task_id: str, version_id: str) -> dict[str, Any] | None:
    with session() as db:
        version = db.get(TaskVersion, version_id)

    if version is None or version.task_id != task_id:
        return None

    return version_dict(version)


def run_list(task_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with session() as db:
        runs = db.query(Run).filter(Run.task_id == task_id).order_by(Run.started_at.desc()).limit(limit).all()

    return [run_dict(r) for r in runs]


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


def version_dict(version: TaskVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "task_id": version.task_id,
        "version_no": version.version_no,
        "status": version.status,
        "spec": version.spec,
        "cards": version.cards,
        "summary": version.summary,
        "patch": version.patch,
        "dry_run": version.dry_run,
        "parent_version_id": version.parent_version_id,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat(),
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
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


def _task_summary(task: Task, current: TaskVersion | None) -> dict[str, Any]:
    spec = current.spec if current else {}

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "current_version_id": task.current_version_id,
        "trigger": spec.get("trigger"),
        "cards": current.cards if current else [],
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
