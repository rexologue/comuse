"""Tool catalog: internal_tools/ and the tools of the plugins' MCP servers the executor
runs (executor/upstream.py, executor/plugins); others read it over MCP (tools/list,
executor/mcp_server.py)."""

from __future__ import annotations

import logging
from typing import Any

from common.settings import StorageSettings
from common.tools import ToolSpec, discover
from common.tools.settings import tool_group
from executor.upstream import Upstream, UpstreamToolSpec


Spec = ToolSpec | UpstreamToolSpec


log = logging.getLogger("registry")


class ToolRegistry:
    def __init__(self, storage: StorageSettings):
        self.storage = storage
        self._specs: dict[str, Spec] = {}
        # Manifests with their hashes, made once per load: a call compares against them.
        self._manifests: dict[str, dict[str, Any]] = {}
        # Servers whose tools are offered here, by their group.
        self.upstreams: dict[str, Upstream] = {}


    def load(self) -> None:
        specs: dict[str, Spec] = dict(discover("internal_tools"))

        # A server's group is its own: one the built-in tools have is not taken.
        taken = {tool_group(name) for name in specs}
        for group, upstream in self.upstreams.items():
            if group in taken:
                log.error("upstream %s skipped: the group is taken by other commands", group)
                continue

            specs.update(upstream.specs())

        self._specs = specs
        self._manifests = {name: spec.manifest() for name, spec in specs.items()}

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str) -> Spec | None:
        return self._specs.get(name)


    def manifest(self, name: str) -> dict[str, Any] | None:
        return self._manifests.get(name)


    def catalog(self) -> list[dict[str, Any]]:
        return [self._manifests[name] for name in sorted(self._manifests)]


    def builtin_groups(self) -> set[str]:
        """Groups a plugin cannot take: the built-in tools'."""
        return {tool_group(name) for name, spec in self._specs.items() if isinstance(spec, ToolSpec)}


    async def connect(self, upstream: Upstream) -> None:
        """Start a server and offer its tools (in place of the group's server there was);
        the catalog follows when it changes them."""
        upstream.on_change = self.load
        await upstream.start()

        if upstream.group in self.upstreams:
            await self.upstreams.pop(upstream.group).stop()

        self.upstreams[upstream.group] = upstream
        self.load()


    async def disconnect(self, group: str) -> None:
        upstream = self.upstreams.pop(group, None)
        if upstream is not None:
            await upstream.stop()
            self.load()


    async def close(self) -> None:
        for group in list(self.upstreams):
            await self.disconnect(group)
