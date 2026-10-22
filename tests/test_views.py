"""main's task views say the schedule in words, as the compiler's cards do."""

from __future__ import annotations

from common.storage.db import session
from common.storage.models import Task, TaskVersion
from main import views


def test_a_task_and_its_versions_say_when_they_run(settings):
    spec = {"title": "Сводка", "trigger": {"type": "cron", "expr": "0 9 * * MON-FRI"}, "steps": []}

    with session() as db:
        db.add(Task(id="task_1", title="Сводка", status="active", current_version_id="ver_1"))
        db.add(TaskVersion(id="ver_1", task_id="task_1", version_no=1, spec=spec, status="approved"))
        db.add(TaskVersion(id="ver_2", task_id="task_1", version_no=2, spec={**spec, "trigger": {"type": "manual"}}, status="draft"))
        db.commit()

    assert views.task_list()[0]["schedule"] == "по будням в 09:00"
    assert views.version_detail("task_1", "ver_2")["schedule"] == "только вручную"


def test_times_from_the_database_say_they_are_utc(settings):
    """SQLite keeps no time zone: without the mark a browser takes UTC for local time."""
    with session() as db:
        db.add(Task(id="task_2", title="Сводка", status="draft"))
        db.commit()

    assert views.task_list()[0]["created_at"].endswith("+00:00")
