"""Read-only views over the shared tables (main never writes tasks or runs)."""

from __future__ import annotations

from typing import Any

from common.cron import describe_schedule
from common.storage.db import session
from common.storage.models import Run, Task, TaskVersion
from common.storage.queries import run_dict
from common.tools.definition import changes


def pending_drafts(session_id: str | None = None) -> list[dict[str, Any]]:
    """What waits for the user's decision: each draft version of a task not archived (a
    task has at most one: a newer draft supersedes the older). With `session_id`, the ones
    made in that chat."""
    with session() as db:
        query = (
            db.query(TaskVersion, Task)
            .join(Task, Task.id == TaskVersion.task_id)
            .filter(TaskVersion.status == "draft", Task.status != "archived")
        )
        if session_id:
            query = query.filter(TaskVersion.session_id == session_id)

        rows = query.order_by(TaskVersion.created_at.desc()).limit(100).all()

    return [
        {
            "task_id": task.id,
            "version_id": version.id,
            "session_id": version.session_id,
            "title": (version.spec or {}).get("title") or task.title,
            "summary": version.summary,
            "cards": version.cards or [],
            "created_at": version.created_at.isoformat(),
        }
        for version, task in rows
    ]


def task_list(*, tool: str | None = None) -> list[dict[str, Any]]:
    """Tasks not archived; with `tool`, only those whose active version, or newest draft
    before the first approval, calls it."""
    with session() as db:
        tasks = db.query(Task).filter(Task.status != "archived").order_by(Task.created_at.desc()).all()
        rows = db.query(TaskVersion).filter(TaskVersion.task_id.in_([t.id for t in tasks])).order_by(TaskVersion.version_no).all()

    versions = {v.id: v for v in rows}
    if tool is None:
        return [_task_summary(t, versions.get(t.current_version_id)) for t in tasks]

    newest = {v.task_id: v for v in rows}
    found = []

    for task in tasks:
        version = versions.get(task.current_version_id) or newest.get(task.id)

        if version is not None and tool in (version.tools or {}):
            found.append(_task_summary(task, versions.get(task.current_version_id)))

    return found


def task_detail(task_id: str, catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """`catalog`: the tools as they are now (the executor's word), for what changed in
    the task's tools since its version was approved; None — not known."""
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
        # [{tool, fields}]: the tools of the active version that are not what it was
        # approved with; it does not run until a version with them is approved.
        "tool_changes": changes(current.tools or {}, catalog) if current and catalog is not None else [],
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


def run_list(task_id: str, *, include_empty: bool = False, limit: int = 50) -> list[dict[str, Any]]:
    with session() as db:
        query = db.query(Run).filter(Run.task_id == task_id)
        if not include_empty:
            query = query.filter(Run.status != "empty")

        runs = query.order_by(Run.started_at.desc()).limit(limit).all()

    return [run_dict(r) for r in runs]


def version_dict(version: TaskVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "task_id": version.task_id,
        "version_no": version.version_no,
        "status": version.status,
        "spec": version.spec,
        "cards": version.cards,
        "schedule": _schedule(version.spec),
        "summary": version.summary,
        "patch": version.patch,
        "session_id": version.session_id,
        "parent_version_id": version.parent_version_id,
        "created_by": version.created_by,
        "created_at": version.created_at.isoformat(),
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
    }


def _task_summary(task: Task, current: TaskVersion | None) -> dict[str, Any]:
    spec = current.spec if current else {}

    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "settings": task.settings or {},
        # The chat the task was built in: open it to keep working on the task there.
        "source_session_id": task.source_session_id,
        "current_version_id": task.current_version_id,
        "trigger": spec.get("trigger"),
        "schedule": _schedule(spec),
        "cards": current.cards if current else [],
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


def _schedule(spec: dict[str, Any]) -> str | None:
    trigger = spec.get("trigger") or {}
    return describe_schedule(trigger.get("type"), trigger.get("expr")) if trigger else None
