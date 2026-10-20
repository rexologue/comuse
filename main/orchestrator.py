"""Multi-service flows live here, not in the services: compile then ask the user to
approve, approve a version then reload triggers. The only approval is the user's, of
a task version: runs never wait for anyone."""

from __future__ import annotations

import logging
from typing import Any

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Approval, Task
from common.utils import new_id, utcnow
from main.events import EventBus
from main.registry import ServiceRegistry


log = logging.getLogger("orchestrator")

class Orchestrator:
    def __init__(self, registry: ServiceRegistry, bus: EventBus):

        self.registry = registry
        self.bus      = bus


    # -----------------------------
    # APPROVALS
    # -----------------------------

    def create_approval(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        run_id: str | None = None,
        task_id: str | None = None,
        task_version_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        with session() as db:
            approval = Approval(
                id=new_id("apr_"),
                kind=kind,
                run_id=run_id,
                task_id=task_id,
                task_version_id=task_version_id,
                session_id=session_id,
                payload=payload,
            )
            db.add(approval)
            db.commit()

        record = approval_dict(approval)
        self.bus.publish("approval.created", record)

        return record


    async def answer(self, approval_id: str, *, approve: bool) -> dict[str, Any]:
        with session() as db:
            approval = db.get(Approval, approval_id)

        if approval is None:
            raise ApiError(status_code=404, message=f"Approval `{approval_id}` not found")

        if approval.status != "pending":
            raise ApiError(status_code=409, message=f"Approval `{approval_id}` is already {approval.status}")

        if approval.kind != "version_approve":
            raise ApiError(status_code=409, message=f"Approval `{approval_id}` of kind `{approval.kind}` cannot be answered")

        if approve:
            await self.approve_version(approval.task_id, approval.task_version_id)
        else:
            await self.reject_version(approval.task_id, approval.task_version_id)

        return self._get(approval_id)


    # -----------------------------
    # TASK VERSIONS
    # -----------------------------

    async def compile(self, body: dict[str, Any]) -> dict[str, Any]:
        result = await self.registry.client("compiler").post("/compile", json=body)

        return self._with_version_approval(result, body.get("session_id"))


    async def patch(self, body: dict[str, Any]) -> dict[str, Any]:
        result = await self.registry.client("compiler").post("/patch", json=body)

        return self._with_version_approval(result, body.get("session_id"))


    async def approve_version(self, task_id: str, version_id: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/versions/{version_id}/approve")

        self._resolve_version_approvals(version_id, "approved")
        await self._reload_triggers(task_id)
        self.bus.publish("task.approved", {"task_id": task_id, "version_id": version_id})

        return result


    async def reject_version(self, task_id: str, version_id: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/versions/{version_id}/reject")

        self._resolve_version_approvals(version_id, "rejected")
        self.bus.publish("task.rejected", {"task_id": task_id, "version_id": version_id})

        return result


    async def set_task_status(self, task_id: str, action: str) -> dict[str, Any]:
        result = await self.registry.client("compiler").post(f"/tasks/{task_id}/{action}")

        await self._reload_triggers(task_id)
        self.bus.publish(f"task.{action}", {"task_id": task_id})

        return result


    def _with_version_approval(self, result: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        approval = self.create_approval(
            kind="version_approve",
            task_id=result["task_id"],
            task_version_id=result["version_id"],
            session_id=session_id,
            payload={"title": result.get("title"), "summary": result.get("summary"), "cards": result.get("cards")},
        )

        return {**result, "approval_id": approval["id"]}


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


    def _resolve(self, approval_id: str, status: str, answer: Any) -> None:
        with session() as db:
            approval = db.get(Approval, approval_id)
            approval.status = status
            approval.answer = answer
            approval.resolved_at = utcnow()
            db.commit()

        self.bus.publish("approval.resolved", approval_dict(approval))


    def _resolve_version_approvals(self, version_id: str, status: str) -> None:
        with session() as db:
            pending = (
                db.query(Approval)
                .filter(Approval.task_version_id == version_id, Approval.status == "pending")
                .all()
            )

        for approval in pending:
            self._resolve(approval.id, status, None)


    def _get(self, approval_id: str) -> dict[str, Any]:
        with session() as db:
            return approval_dict(db.get(Approval, approval_id))


def approval_dict(approval: Approval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "kind": approval.kind,
        "status": approval.status,
        "run_id": approval.run_id,
        "task_id": approval.task_id,
        "task_version_id": approval.task_version_id,
        "session_id": approval.session_id,
        "payload": approval.payload,
        "answer": approval.answer,
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
    }
