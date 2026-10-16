"""Decides *when* a task runs; the runner decides how. Every fire goes through main."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from common.clients.main_client import MainClient
from common.errors import ApiError
from common.pathrefs import normalize_folder
from common.schemas.task import TaskSpec
from common.settings import Settings
from common.storage.db import session
from common.storage.models import Task, TaskVersion, Trigger


log = logging.getLogger("scheduler")

# A file is considered complete once nothing new appeared in the folder for this long.
_FILE_DEBOUNCE_SEC = 5
# When the task is still busy with the previous run, try the fire again after this.
_BUSY_RETRY_SEC = 60


class TaskScheduler:
    def __init__(self, settings: Settings, main: MainClient):

        self.settings = settings
        self.main     = main

        self._scheduler = AsyncIOScheduler()
        self._observer = PollingObserver() if settings.scheduler.watch_polling else Observer()
        self._watches: dict[str, Any] = {}
        self._pending_files: dict[str, list[str]] = {}
        self._debounce: dict[str, asyncio.TimerHandle] = {}
        self._loop: asyncio.AbstractEventLoop | None = None


    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._scheduler.start()
        self._observer.start()


    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        self._observer.stop()
        self._observer.join(timeout=5)


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

            spec = TaskSpec.model_validate(version.spec)
            self._schedule(task.id, spec)
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
                    "watching": task_id in self._watches,
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
                args=[task_id, "cron", {}],
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=3600,
            )
            self._save_trigger(task_id, trigger.type, trigger.model_dump(), next_fire=job.next_run_time)

        elif trigger.type == "file_appeared":
            self._watch(task_id, trigger.folder or "", trigger.pattern)
            self._save_trigger(task_id, trigger.type, trigger.model_dump())

        elif trigger.type == "new_email":
            job = self._scheduler.add_job(
                self._poll_mail,
                IntervalTrigger(minutes=self.settings.scheduler.mail_poll_minutes),
                id=task_id,
                args=[task_id],
                replace_existing=True,
                coalesce=True,
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
        removed = False

        if self._scheduler.get_job(task_id):
            self._scheduler.remove_job(task_id)
            removed = True

        watch = self._watches.pop(task_id, None)
        if watch is not None:
            self._observer.unschedule(watch)
            removed = True

        return removed


    def _job_ids(self) -> set[str]:
        return {job.id for job in self._scheduler.get_jobs()} | set(self._watches)


    async def _fire(self, task_id: str, trigger: str, data: dict[str, Any]) -> None:
        try:
            run = await self.main.post(
                "/runner/runs",
                json={"task_id": task_id, "trigger": trigger, "trigger_data": data},
            )
            log.info("fired %s (%s): run %s", task_id, trigger, run["id"])

        except ApiError as e:
            if e.status_code == 409 and trigger == "file_appeared":
                # New files must not be lost while the previous run is still going.
                log.info("task %s busy, retrying file trigger in %ds", task_id, _BUSY_RETRY_SEC)
                self._loop.call_later(_BUSY_RETRY_SEC, lambda: asyncio.ensure_future(self._fire(task_id, trigger, data)))
                return

            log.warning("cannot fire %s (%s): %s", task_id, trigger, e.message)

        job = self._scheduler.get_job(task_id)
        if job is not None:
            self._update_trigger(task_id, next_fire=job.next_run_time)


    # -----------------------------
    # FILE TRIGGER
    # -----------------------------

    def _watch(self, task_id: str, folder: str, pattern: str) -> None:
        path = (self.settings.storage.workspace_dir / normalize_folder(folder)).resolve()
        path.mkdir(parents=True, exist_ok=True)

        handler = _FolderHandler(lambda p: self._on_file(task_id, p), pattern)
        self._watches[task_id] = self._observer.schedule(handler, str(path), recursive=False)


    def _on_file(self, task_id: str, path: Path) -> None:
        """Called from the watchdog thread; batches files into one run per quiet period."""
        ref = path.resolve().relative_to(self.settings.storage.workspace_dir.resolve()).as_posix()

        def schedule() -> None:
            files = self._pending_files.setdefault(task_id, [])
            if ref not in files:
                files.append(ref)

            if task_id in self._debounce:
                self._debounce[task_id].cancel()

            self._debounce[task_id] = self._loop.call_later(_FILE_DEBOUNCE_SEC, lambda: asyncio.ensure_future(self._flush_files(task_id)))

        self._loop.call_soon_threadsafe(schedule)


    async def _flush_files(self, task_id: str) -> None:
        self._debounce.pop(task_id, None)
        files = self._pending_files.pop(task_id, [])

        if files:
            await self._fire(task_id, "file_appeared", {"files": [{"path": f, "name": Path(f).name} for f in files]})


    # -----------------------------
    # MAIL TRIGGER
    # -----------------------------

    async def _poll_mail(self, task_id: str) -> None:
        with session() as db:
            task = db.get(Task, task_id)
            version = db.get(TaskVersion, task.current_version_id) if task else None
            row = db.get(Trigger, task_id)

        if version is None or row is None:
            return

        spec = TaskSpec.model_validate(version.spec)
        trigger = spec.trigger

        try:
            response = await self.main.post(
                "/executor/execute",
                json={
                    "tool": "mail.search",
                    "args": {
                        "account": trigger.account,
                        "sender": trigger.mail_filter.get("from"),
                        "subject": trigger.mail_filter.get("subject"),
                        "limit": 50,
                    },
                    "context": {"mode": "run", "permissions": spec.permissions.model_dump()},
                },
            )

        except ApiError as e:
            log.warning("mail poll for %s failed: %s", task_id, e.message)
            return

        if response["status"] != "ok":
            log.warning("mail poll for %s failed: %s", task_id, response.get("error"))
            return

        messages = response["output"]["messages"]
        last_uid = (row.state or {}).get("last_uid")
        newest = max((m["uid"] for m in messages), default=last_uid)

        # The first poll only sets the cursor: old mail must not trigger a run.
        fresh = [m for m in messages if last_uid is not None and m["uid"] > last_uid]
        self._update_trigger(task_id, state={"last_uid": newest})

        if fresh:
            await self._fire(task_id, "new_email", {"messages": fresh})


    # -----------------------------
    # TRIGGERS TABLE (scheduler is its only writer)
    # -----------------------------

    def _save_trigger(self, task_id: str, kind: str, config: dict[str, Any], next_fire=None) -> None:
        with session() as db:
            row = db.get(Trigger, task_id) or Trigger(id=task_id, task_id=task_id, state={})

            # A changed trigger restarts its cursor (e.g. a different mailbox).
            if row.type != kind or row.config != config:
                row.state = {}

            row.type = kind
            row.config = config
            row.enabled = True
            row.next_fire_at = next_fire
            db.add(row)
            db.commit()


    def _update_trigger(self, task_id: str, *, next_fire=None, state: dict[str, Any] | None = None) -> None:
        with session() as db:
            row = db.get(Trigger, task_id)
            if row is None:
                return

            if next_fire is not None:
                row.next_fire_at = next_fire

            if state is not None:
                row.state = state

            db.commit()


class _FolderHandler(FileSystemEventHandler):
    def __init__(self, callback, pattern: str):
        self.callback = callback
        self.pattern  = pattern


    def on_created(self, event: FileSystemEvent) -> None:
        self._maybe(event.src_path, event.is_directory)


    def on_moved(self, event: FileSystemEvent) -> None:
        self._maybe(event.dest_path, event.is_directory)


    def _maybe(self, raw_path, is_directory: bool) -> None:
        path = Path(raw_path if isinstance(raw_path, str) else raw_path.decode())

        # Office lock files (~$report.docx) and temp files are not new documents.
        if is_directory or path.name.startswith(("~$", ".")) or path.suffix.lower() in (".tmp", ".part", ".crdownload"):
            return

        if fnmatch.fnmatch(path.name, self.pattern):
            self.callback(path)
