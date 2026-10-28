"""The tool catalog as a service sees it: the executor's MCP tools/list (through main),
fetched again after a minute or when asked to."""

from __future__ import annotations

import time
from typing import Any, Callable

from common.clients.main_client import MainClient
from common.tools.definition import Catalog


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
            catalog = await self.main.tools()
            self._manifests = Catalog({name: manifest for name, manifest in catalog.items() if self.keep(manifest)}, getattr(catalog, "unavailable", None))
            self._loaded_at = time.monotonic()

        return self._manifests
