"""tasks and task_versions; compiler is their only writer."""

from __future__ import annotations

from typing import Any

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.storage.db import session
from common.storage.models import Task, TaskVersion
from common.utils import new_id, utcnow


def create_task(spec: TaskSpec, *, session_id: str | None, cards: list[dict[str, Any]], summary: str) -> TaskVersion:
    with session() as db:
        task = Task(
            id=new_id("task_"),
            title=spec.title,
            description=spec.description,
            status="draft",
            source_session_id=session_id,
        )
        version = TaskVersion(
            id=new_id("ver_"),
            task_id=task.id,
            version_no=1,
            spec=spec.dump(),
            status="draft",
            cards=cards,
            summary=summary,
            created_by="compile",
        )
        db.add_all([task, version])
        db.commit()

    return version


def add_version(
    task_id: str,
    spec: TaskSpec,
    *,
    parent: TaskVersion,
    patch: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    summary: str,
) -> TaskVersion:
    with session() as db:
        last_no = (
            db.query(TaskVersion.version_no)
            .filter(TaskVersion.task_id == task_id)
            .order_by(TaskVersion.version_no.desc())
            .first()
        )

        version = TaskVersion(
            id=new_id("ver_"),
            task_id=task_id,
            version_no=(last_no[0] if last_no else 0) + 1,
            spec=spec.dump(),
            status="draft",
            parent_version_id=parent.id,
            patch=patch,
            cards=cards,
            summary=summary,
            created_by="patch",
        )
        db.add(version)
        db.commit()

    return version


def set_dry_run(version_id: str, result: dict[str, Any]) -> None:
    with session() as db:
        version = db.get(TaskVersion, version_id)
        version.dry_run = result
        db.commit()


def get_task(task_id: str) -> Task:
    with session() as db:
        task = db.get(Task, task_id)

    if task is None:
        raise ApiError(status_code=404, message=f"Task `{task_id}` not found")

    return task


def get_version(task_id: str, version_id: str) -> TaskVersion:
    with session() as db:
        version = db.get(TaskVersion, version_id)

    if version is None or version.task_id != task_id:
        raise ApiError(status_code=404, message=f"Version `{version_id}` of task `{task_id}` not found")

    return version


def base_version(task_id: str) -> TaskVersion:
    """What a patch builds on: the active version, else the newest draft."""
    task = get_task(task_id)

    with session() as db:
        if task.current_version_id:
            return db.get(TaskVersion, task.current_version_id)

        version = (
            db.query(TaskVersion)
            .filter(TaskVersion.task_id == task_id, TaskVersion.status == "draft")
            .order_by(TaskVersion.version_no.desc())
            .first()
        )

    if version is None:
        raise ApiError(status_code=409, message=f"Task `{task_id}` has no version to change")

    return version


def approve(task_id: str, version_id: str) -> Task:
    with session() as db:
        task = db.get(Task, task_id)
        version = db.get(TaskVersion, version_id)

        if task is None or version is None or version.task_id != task_id:
            raise ApiError(status_code=404, message=f"Version `{version_id}` of task `{task_id}` not found")

        if version.status != "draft":
            raise ApiError(status_code=409, message=f"Version is {version.status}, only drafts can be approved")

        if task.current_version_id:
            previous = db.get(TaskVersion, task.current_version_id)
            if previous is not None:
                previous.status = "superseded"

        version.status = "approved"
        version.approved_at = utcnow()

        task.current_version_id = version.id
        task.title = version.spec.get("title", task.title)
        task.description = version.spec.get("description", task.description)
        if task.status in ("draft", "archived"):
            task.status = "active"

        db.commit()

    return task


def reject(task_id: str, version_id: str) -> TaskVersion:
    with session() as db:
        version = db.get(TaskVersion, version_id)

        if version is None or version.task_id != task_id:
            raise ApiError(status_code=404, message=f"Version `{version_id}` of task `{task_id}` not found")

        if version.status != "draft":
            raise ApiError(status_code=409, message=f"Version is {version.status}, only drafts can be rejected")

        version.status = "rejected"
        db.commit()

    return version


_TRANSITIONS = {
    "pause": ({"active"}, "paused"),
    "resume": ({"paused"}, "active"),
    "archive": ({"draft", "active", "paused"}, "archived"),
}


def set_status(task_id: str, action: str) -> Task:
    allowed, target = _TRANSITIONS[action]

    with session() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise ApiError(status_code=404, message=f"Task `{task_id}` not found")

        if task.status not in allowed:
            raise ApiError(status_code=409, message=f"Cannot {action} a task that is {task.status}")

        task.status = target
        db.commit()

    return task
