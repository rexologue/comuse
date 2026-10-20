"""Event bus: every event is stored, then fanned out to live SSE subscribers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from common.retention import cutoff
from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import Event


log = logging.getLogger("events")

_QUEUE_SIZE = 256


class EventBus:
    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()


    def publish(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with session() as db:
            event = Event(type=event_type, payload=payload)
            db.add(event)
            db.commit()

        record = to_dict(event)

        for queue in list(self._subscribers):
            try:
                queue.put_nowait(record)

            except asyncio.QueueFull:
                # A stalled client must not block the bus; it can replay with ?since=.
                log.warning("dropping event %d for a slow subscriber", event.id)

        return record


    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self._subscribers.add(queue)

        return queue


    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)


def sweep(storage: StorageSettings) -> int:
    """Events older than RETENTION_DAYS go; a client replays only recent ones anyway."""
    with session() as db:
        removed = db.query(Event).filter(Event.created_at < cutoff(storage)).delete(synchronize_session=False)
        db.commit()

    return removed


def recent(since: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with session() as db:
        query = db.query(Event)
        if since is not None:
            query = query.filter(Event.id > since).order_by(Event.id.asc())
        else:
            query = query.order_by(Event.id.desc())

        rows = query.limit(limit).all()

    records = [to_dict(row) for row in rows]

    return records if since is not None else list(reversed(records))


def to_dict(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
