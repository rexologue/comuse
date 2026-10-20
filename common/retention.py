"""Old data goes on a schedule. Each service sweeps only what it writes: runner its runs
and their logs, executor the files under data:outputs (runs' and chats' alike), main its
events. One age for all of them: RETENTION_DAYS."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable

from common.settings import StorageSettings
from common.utils import utcnow


log = logging.getLogger("retention")

_SWEEP_EVERY_SEC = 6 * 3600


def cutoff(storage: StorageSettings) -> datetime:
    return utcnow() - timedelta(days=storage.retention_days)


async def sweep_forever(what: str, sweep: Callable[[], int]) -> None:
    """Run `sweep` at start and every few hours; it returns how many items went."""
    while True:
        try:
            removed = await asyncio.to_thread(sweep)
            if removed:
                log.info("removed %d old %s", removed, what)

        except Exception:
            log.exception("sweeping old %s failed", what)

        await asyncio.sleep(_SWEEP_EVERY_SEC)
