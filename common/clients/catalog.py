"""The tool catalog as a service sees it: main's /tools (the executor's manifests),
fetched again after a minute or when asked to."""

from __future__ import annotations

import time
from typing import Any, Callable

from common.clients.main_client import MainClient


_TTL_SEC = 60


class CatalogCache:
    def __init__(self, main: MainClient, *, keep: Callable[[dict[str, Any]], bool] = lambda manifest: True):

        self.main = main
        # Which manifests this service works with (the compiler leaves chat-only ones out).
        self.keep = keep

        self._manifests: dict[str, dict[str, Any]] = {}
        self._loaded_at = 0.0


    async def get(self, *, fresh: bool = False) -> dict[str, dict[str, Any]]:
        if fresh or not self._manifests or time.monotonic() - self._loaded_at > _TTL_SEC:
            data = await self.main.get("/tools")
            self._manifests = {tool["name"]: tool for tool in data["tools"] if self.keep(tool)}
            self._loaded_at = time.monotonic()

        return self._manifests
