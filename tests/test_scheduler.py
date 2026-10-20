"""The scheduler only knows when tasks run; one unreadable task must not stop the rest."""

from __future__ import annotations

from common.storage.db import session
from common.storage.models import Task, TaskVersion
from scheduler.jobs import TaskScheduler


GOOD = {"title": "Сводка", "trigger": {"type": "cron", "expr": "0 9 * * MON"}, "steps": [{"id": "files", "tool": "fs.list", "args": {}}]}

# Approved under an older schema: a field the current TaskSpec no longer has.
OLD = {**GOOD, "permissions": {"mail_to": ["boss@company.local"]}}


def _task(task_id: str, spec: dict) -> None:
    with session() as db:
        db.add(Task(id=task_id, title=spec["title"], status="active", current_version_id=f"ver_{task_id}"))
        db.add(TaskVersion(id=f"ver_{task_id}", task_id=task_id, version_no=1, spec=spec, status="approved"))
        db.commit()


async def test_a_task_from_an_older_schema_does_not_stop_the_others(settings):
    _task("task_old", OLD)
    _task("task_good", GOOD)

    scheduler = TaskScheduler(settings, main=None)
    scheduler.start()

    try:
        assert scheduler.reload() == {"scheduled": 1, "removed": 0}
        assert [job["task_id"] for job in scheduler.jobs()] == ["task_good"]

    finally:
        scheduler.shutdown()


async def test_a_task_with_weekdays_as_numbers_is_not_scheduled(settings):
    _task("task_numbers", {**GOOD, "trigger": {"type": "cron", "expr": "0 9 * * 1"}})

    scheduler = TaskScheduler(settings, main=None)
    scheduler.start()

    try:
        assert scheduler.reload() == {"scheduled": 0, "removed": 0}

    finally:
        scheduler.shutdown()
