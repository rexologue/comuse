"""tasks and task_versions; compiler is their only writer."""

from __future__ import annotations

from typing import Any

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.storage.db import session
from common.storage.models import Task, TaskVersion
from common.utils import new_id, utcnow
from compiler import task_settings


def create_task(
    spec: TaskSpec,
    *,
    session_id: str | None,
    cards: list[dict[str, Any]],
    summary: str,
    settings: dict[str, Any],
) -> TaskVersion:
    with session() as db:
        task = Task(
            id=new_id("task_"),
            title=spec.title,
            description=spec.description,
            status="draft",
            source_session_id=session_id,
            settings=settings,
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
            session_id=session_id,
        )
        db.add_all([task, version])
        db.commit()

    return version


def add_version(
    task_id: str,
    spec: TaskSpec,
    *,
    parent: TaskVersion,
    patch: list[dict[str, Any]] | None,
    cards: list[dict[str, Any]],
    summary: str,
    created_by: str,
    session_id: str | None,
) -> TaskVersion:
    with session() as db:
        last_no = (
            db.query(TaskVersion.version_no)
            .filter(TaskVersion.task_id == task_id)
            .order_by(TaskVersion.version_no.desc())
            .first()
        )

        # The new draft carries the older drafts' changes (it was built on the newest);
        # they are no longer something to approve.
        db.query(TaskVersion).filter(TaskVersion.task_id == task_id, TaskVersion.status == "draft").update(
            {"status": "superseded"}, synchronize_session=False
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
            created_by=created_by,
            session_id=session_id,
        )
        db.add(version)
        db.commit()

    return version


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
    """What a change builds on: the newest draft after the active version (the user is
    still shaping it: a second change must keep the first), else the active version."""
    task = get_task(task_id)

    with session() as db:
        current = db.get(TaskVersion, task.current_version_id) if task.current_version_id else None

        query = db.query(TaskVersion).filter(TaskVersion.task_id == task_id, TaskVersion.status == "draft")
        if current is not None:
            query = query.filter(TaskVersion.version_no > current.version_no)

        version = query.order_by(TaskVersion.version_no.desc()).first() or current

    if version is None:
        raise ApiError(status_code=409, message=f"Task `{task_id}` has no version to change")

    return version


def approve(task_id: str, version_id: str, catalog: dict[str, dict[str, Any]]) -> Task:
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
        # The new version may use other tools: settings follow, the user's values stay.
        task.settings = task_settings.sync(task.settings or {}, TaskSpec.model_validate(version.spec), catalog)
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


def set_settings(task_id: str, settings: dict[str, Any]) -> Task:
    with session() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise ApiError(status_code=404, message=f"Task `{task_id}` not found")

        task.settings = settings
        db.commit()

    return task


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
