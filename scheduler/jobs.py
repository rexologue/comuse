"""Decides *when* a task runs; the runner decides how. Every fire goes through main."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from common.clients.main_client import MainClient
from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.settings import Settings
from common.storage.db import session
from common.storage.models import Task, TaskVersion, Trigger


log = logging.getLogger("scheduler")


class TaskScheduler:
    def __init__(self, settings: Settings, main: MainClient):

        self.settings = settings
        self.main     = main

        self._scheduler = AsyncIOScheduler()


    def start(self) -> None:
        self._scheduler.start()


    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)


    def reload(self, task_id: str | None = None) -> dict[str, Any]:
        """Bring jobs in line with the active tasks (all, or just one)."""
        with session() as db:
            query = db.query(Task)
            if task_id:
                query = query.filter(Task.id == task_id)

            tasks = query.all()
            versions = {
                v.id: v
                for v in db.query(TaskVersion).filter(TaskVersion.id.in_([t.current_version_id for t in tasks if t.current_version_id]))
            }

        scheduled, removed = 0, 0

        for task in tasks:
            version = versions.get(task.current_version_id)

            if task.status != "active" or version is None:
                removed += self._remove(task.id)
                continue

            # One task the scheduler cannot read (a spec from an older schema, a bad cron)
            # must not keep every other task from being scheduled.
            try:
                self._schedule(task.id, TaskSpec.model_validate(version.spec))

            except ValueError as e:
                log.error("cannot schedule %s: %s", task.id, e)
                removed += self._remove(task.id)
                continue

            scheduled += 1

        if task_id is None:
            known = {t.id for t in tasks}
            for stale_id in [job_id for job_id in self._job_ids() if job_id not in known]:
                removed += self._remove(stale_id)

        return {"scheduled": scheduled, "removed": removed}


    def jobs(self) -> list[dict[str, Any]]:
        with session() as db:
            triggers = {t.task_id: t for t in db.query(Trigger).filter(Trigger.enabled.is_(True))}

        out = []
        for task_id, trigger in triggers.items():
            job = self._scheduler.get_job(task_id)
            out.append(
                {
                    "task_id": task_id,
                    "type": trigger.type,
                    "config": trigger.config,
                    "next_run_time": job.next_run_time.isoformat() if job and job.next_run_time else None,
                }
            )

        return out


    # -----------------------------
    # JOBS
    # -----------------------------

    def _schedule(self, task_id: str, spec: TaskSpec) -> None:
        trigger = spec.trigger
        self._unschedule(task_id)

        if trigger.type == "cron":
            job = self._scheduler.add_job(
                self._fire,
                CronTrigger.from_crontab(trigger.expr),
                id=task_id,
                args=[task_id],
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=3600,
            )
            self._save_trigger(task_id, trigger.type, trigger.model_dump(), next_fire=job.next_run_time)

        else:
            self._save_trigger(task_id, trigger.type, trigger.model_dump())

        log.info("scheduled %s: %s", task_id, trigger.model_dump(exclude_defaults=True))


    def _remove(self, task_id: str) -> int:
        had_job = self._unschedule(task_id)

        with session() as db:
            row = db.get(Trigger, task_id)
            if row is not None and row.enabled:
                row.enabled = False
                db.commit()
                had_job = True

        return int(had_job)


    def _unschedule(self, task_id: str) -> bool:
        if self._scheduler.get_job(task_id) is None:
            return False

        self._scheduler.remove_job(task_id)
        return True


    def _job_ids(self) -> set[str]:
        return {job.id for job in self._scheduler.get_jobs()}


    async def _fire(self, task_id: str) -> None:
        try:
            run = await self.main.post("/runner/runs", json={"task_id": task_id, "trigger": "cron"})
            log.info("fired %s: run %s", task_id, run["id"])

        except ApiError as e:
            log.warning("cannot fire %s: %s", task_id, e.message)

        job = self._scheduler.get_job(task_id)
        if job is not None:
            self._update_trigger(task_id, next_fire=job.next_run_time)


    # -----------------------------
    # TRIGGERS TABLE (scheduler is its only writer)
    # -----------------------------

    def _save_trigger(self, task_id: str, kind: str, config: dict[str, Any], next_fire=None) -> None:
        with session() as db:
            row = db.get(Trigger, task_id) or Trigger(id=task_id, task_id=task_id)
            row.type = kind
            row.config = config
            row.enabled = True
            row.next_fire_at = next_fire
            db.add(row)
            db.commit()


    def _update_trigger(self, task_id: str, *, next_fire=None) -> None:
        with session() as db:
            row = db.get(Trigger, task_id)
            if row is None:
                return

            if next_fire is not None:
                row.next_fire_at = next_fire

            db.commit()
