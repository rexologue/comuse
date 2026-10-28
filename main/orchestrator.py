"""Multi-service flows live here, not in the services: a new draft is announced to the
user, an approved version reloads the triggers, a task whose tools changed stops. The only approval is the user's, of a
task version, and it is the version's own status (compiler): a draft waits for it, an
approved one is active, a newer draft supersedes an older one. Runs never wait."""

from __future__ import annotations

import logging
from typing import Any

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Task
from main.events import EventBus
from main.registry import ServiceRegistry


log = logging.getLogger("orchestrator")


class Orchestrator:
    def __init__(self, registry: ServiceRegistry, bus: EventBus):

        self.registry = registry
        self.bus      = bus


    # -----------------------------
    # TASK VERSIONS
    # -----------------------------

    async def compile(self, body: dict[str, Any]) -> dict[str, Any]:
        result = await self.registry.client("compiler").post("/compile", json=body)

        return self._announce_draft(result, body.get("session_id"))


    async def patch(self, body: dict[str, Any]) -> dict[str, Any]:
        result = await self.registry.client("compiler").post("/patch", json=body)

        return self._announce_draft(result, body.get("session_id"))


    async def edit(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/versions", json=body)

        return self._announce_draft(result, None)


    async def refresh_tools(self, task_id: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/tools/refresh")

        return self._announce_draft(result, None)


    async def approve_version(self, task_id: str, version_id: str) -> dict[str, Any]:
        # The compiler approves only a draft: one superseded meanwhile is refused (409).
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/versions/{version_id}/approve")

        await self._reload_triggers(task_id)
        self.bus.publish("task.approved", {"task_id": task_id, "version_id": version_id})

        return result


    async def reject_version(self, task_id: str, version_id: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/versions/{version_id}/reject")

        self.bus.publish("task.rejected", {"task_id": task_id, "version_id": version_id})

        return result


    async def set_task_status(self, task_id: str, action: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/{action}")

        await self._reload_triggers(task_id)
        self.bus.publish(f"task.{action}", {"task_id": task_id})

        return result


    def _announce_draft(self, result: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        """A new draft waits for the user: whoever shows what waits (the chat it was made
        in, the event feed) hears of it."""
        self.bus.publish(
            "task.draft",
            {"task_id": result["task_id"], "version_id": result["version_id"], "session_id": session_id, "title": result.get("title")},
        )

        return result


    async def tools_changed(self, *, task_id: str, run_id: str, title: str, tools: list[str], dry_run: bool, gone: list[str] = ()) -> dict[str, Any]:
        """The runner refused a run: tools of the task are not the ones approved. The task
        pauses (its schedule with it) until the user reads the changes and approves the
        task with them; a dry run only says so in its own result."""
        self.bus.publish("task.tools_changed", {"task_id": task_id, "run_id": run_id, "tools": tools, "dry_run": dry_run})

        if dry_run:
            return {"paused": False}

        try:
            await self.set_task_status(task_id, "pause")
            paused = True

        except ApiError as e:
            # Paused already, or never active: nothing more to stop.
            if e.status_code != 409:
                raise
            paused = False

        changed = [tool for tool in tools if tool not in gone]
        said = []
        if changed:
            said.append(f"изменились команды {', '.join(f'`{tool}`' for tool in changed)}")
        if gone:
            said.append(f"команд {', '.join(f'`{tool}`' for tool in gone)} больше нет")
        advice = (
            "Уберите их шаги из задачи (попросите в этом чате) или верните команды, потом возобновите её."
            if gone else "Посмотрите изменения в карточке задачи, одобрите её с ними и возобновите."
        )

        await self.notify(
            task_id=task_id,
            run_id=run_id,
            title=title,
            message=f"{' и '.join(said)}, и задача не запущена" + (" и поставлена на паузу" if paused else "") + f". {advice}",
        )

        return {"paused": paused}


    # -----------------------------
    # NOTIFICATIONS
    # -----------------------------

    async def notify(self, *, task_id: str, run_id: str, title: str, message: str) -> dict[str, Any]:
        """A task run's message reaches the chat the task was built in (and the event
        stream); the run log already has it."""
        self.bus.publish("notify", {"task_id": task_id, "run_id": run_id, "title": title, "message": message})

        with session() as db:
            task = db.get(Task, task_id)
            session_id = task.source_session_id if task else None

        if session_id is None:
            return {"delivered": False}

        try:
            await self.registry.client("agent_loop").post(
                f"/sessions/{session_id}/messages",
                json={"content": f"Задача «{title}»: {message}"},
            )

        except ApiError as e:
            log.warning("notification for task %s not delivered to chat %s: %s", task_id, session_id, e.message)
            return {"delivered": False}

        return {"delivered": True, "session_id": session_id}


    async def _reload_triggers(self, task_id: str) -> None:
        # A scheduler hiccup must not undo an approval; it reloads on its own start too.
        try:
            await self.registry.client("scheduler").post("/reload", json={"task_id": task_id})

        except ApiError as e:
            log.warning("scheduler reload for %s failed: %s", task_id, e.message)
