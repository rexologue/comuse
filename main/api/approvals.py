from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from common.storage.db import session
from common.storage.models import Approval, Setting
from main import events
from main.auth import require_client
from main.orchestrator import approval_dict


router = APIRouter(dependencies=[Depends(require_client)])

# SSE comment ping so proxies and browsers keep the stream open.
_PING_SEC = 15


class AnswerBody(BaseModel):
    approve: bool = True


# -----------------------------
# APPROVALS
# -----------------------------

@router.get("/approvals", summary="Task versions waiting for the user's approval")
async def list_approvals(status: str | None = "pending", session_id: str | None = None):
    with session() as db:
        query = db.query(Approval)
        if status:
            query = query.filter(Approval.status == status)
        if session_id:
            query = query.filter(Approval.session_id == session_id)

        rows = query.order_by(Approval.created_at.desc()).limit(100).all()

    return {"approvals": [approval_dict(a) for a in rows]}


@router.post("/approvals/{approval_id}/answer", summary="Approve or reject a task version")
async def answer(approval_id: str, body: AnswerBody, request: Request):
    return await request.app.state.orchestrator.answer(approval_id, approve=body.approve)


# -----------------------------
# EVENTS
# -----------------------------

@router.get("/events/recent", summary="Last events as JSON")
async def recent_events(since: int | None = None, limit: int = 100):
    return {"events": events.recent(since=since, limit=limit)}


@router.get("/events", summary="Live event stream (SSE); ?since=<id> replays first")
async def stream_events(request: Request, since: int | None = None):
    bus = request.app.state.bus

    async def generator():
        queue = bus.subscribe()

        try:
            if since is not None:
                for record in events.recent(since=since, limit=500):
                    yield {"id": str(record["id"]), "event": record["type"], "data": json.dumps(record, ensure_ascii=False)}

            while True:
                if await request.is_disconnected():
                    break

                try:
                    record = await asyncio.wait_for(queue.get(), timeout=_PING_SEC)

                except TimeoutError:
                    yield {"comment": "ping"}
                    continue

                yield {"id": str(record["id"]), "event": record["type"], "data": json.dumps(record, ensure_ascii=False)}

        finally:
            bus.unsubscribe(queue)

    return EventSourceResponse(generator())


# -----------------------------
# SETTINGS
# -----------------------------

@router.get("/settings", summary="User-level settings")
async def get_settings():
    with session() as db:
        return {row.key: row.value for row in db.query(Setting)}


@router.put("/settings", summary="Update user-level settings")
async def put_settings(body: dict[str, Any]):
    with session() as db:
        for key, value in body.items():
            db.merge(Setting(key=key, value=value))

        db.commit()

    return await get_settings()
