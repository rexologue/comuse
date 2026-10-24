"""Where every service lives. Services announce themselves (their port may be any:
the launcher hands out free ones); main never guesses. Until a service has registered,
calls to it get 503."""

from __future__ import annotations

import asyncio
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
        # By service name: one client per service, for its current address.
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

        url = self._urls.get(name)
        if url is None:
            raise ApiError(status_code=503, message=f"Сервис `{name}` ещё не зарегистрировался в main: он запускается или упал")

        return url


    def client(self, name: str) -> MainClient:
        """Internal-token client bound to a service's current address; the one for an
        address it left (a restart on another port) is closed."""
        url = self.url(name)
        current = self._clients.get(name)

        if current is None or current.base_url != url:
            if current is not None:
                asyncio.get_running_loop().create_task(current.close())

            self._clients[name] = MainClient(base_url=url, internal_token=self.internal_token)

        return self._clients[name]


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
