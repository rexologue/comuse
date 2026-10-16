"""Where every service lives. Services announce themselves; main never guesses
addresses except the local default used before the first registration."""

from __future__ import annotations

import logging
from datetime import datetime

from common.clients.main_client import MainClient
from common.errors import ApiError
from common.settings import SERVICE_PORTS
from common.utils import utcnow


log = logging.getLogger("registry")


class ServiceRegistry:
    def __init__(self, internal_token: str):

        self.internal_token = internal_token

        self._urls: dict[str, str] = {}
        self._seen: dict[str, datetime] = {}
        self._clients: dict[str, MainClient] = {}


    def register(self, name: str, url: str) -> None:
        if name not in SERVICE_PORTS or name == "main":
            raise ApiError(status_code=400, message=f"Unknown service `{name}`")

        url = url.rstrip("/")
        if self._urls.get(name) != url:
            log.info("service registered: %s → %s", name, url)

        self._urls[name] = url
        self._seen[name] = utcnow()


    def url(self, name: str) -> str:
        if name not in SERVICE_PORTS:
            raise ApiError(status_code=404, message=f"Unknown service `{name}`")

        return self._urls.get(name) or f"http://127.0.0.1:{SERVICE_PORTS[name]}"


    def client(self, name: str) -> MainClient:
        """Internal-token client bound to a service's current address."""
        url = self.url(name)

        if url not in self._clients:
            self._clients[url] = MainClient(base_url=url, internal_token=self.internal_token)

        return self._clients[url]


    def status(self) -> dict[str, dict[str, str | None]]:
        return {
            name: {
                "url": self._urls.get(name),
                "last_seen": self._seen[name].isoformat() if name in self._seen else None,
            }
            for name in SERVICE_PORTS
            if name != "main"
        }


    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()
