from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from main import events, views
from main.auth import require_client


router = APIRouter(dependencies=[Depends(require_client)])

# SSE comment ping so proxies and browsers keep the stream open.
_PING_SEC = 15


# -----------------------------
# APPROVALS
# -----------------------------

@router.get("/approvals", summary="Draft task versions waiting for the user's approval (the draft is the approval)")
async def list_approvals(session_id: str | None = None):
    # Approving or rejecting one: POST /tasks/{id}/versions/{version_id}/approve | reject.
    return {"approvals": views.pending_drafts(session_id)}


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

