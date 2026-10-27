"""Tool catalog: internal_tools/ plus the current code of every own command
(common/external_tools); others read it over HTTP (GET /tools)."""

from __future__ import annotations

import logging
from typing import Any

from common.external_tools import store
from common.external_tools.spec import ExternalToolSpec
from common.settings import StorageSettings
from common.tools import ToolSpec, discover
from common.tools.settings import tool_group


log = logging.getLogger("registry")


class ToolRegistry:
    def __init__(self, storage: StorageSettings):
        self.storage = storage
        self._specs: dict[str, ToolSpec | ExternalToolSpec] = {}
        # Manifests with their hashes, made once per load: a call compares against them.
        self._manifests: dict[str, dict[str, Any]] = {}


    def load(self) -> None:
        specs: dict[str, ToolSpec | ExternalToolSpec] = dict(discover("internal_tools"))

        for row in store.current_rows():
            try:
                spec = ExternalToolSpec.of(row, self.storage)

            except Exception as e:
                # One broken manifest must not take the other commands down.
                log.error("external tool %s skipped: %s", row.name, e)
                continue

            specs[spec.name] = spec

        self._specs = specs
        self._manifests = {name: spec.manifest() for name, spec in specs.items()}

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str) -> ToolSpec | ExternalToolSpec | None:
        return self._specs.get(name)


    def manifest(self, name: str) -> dict[str, Any] | None:
        return self._manifests.get(name)


    def catalog(self) -> list[dict[str, Any]]:
        return [self._manifests[name] for name in sorted(self._manifests)]


    def builtin_groups(self) -> set[str]:
        """Groups of the built-in tools: an own command takes a group of its own."""
        return {tool_group(name) for name, spec in self._specs.items() if isinstance(spec, ToolSpec)}
