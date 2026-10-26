"""Tool catalog: internal_tools/ plus the current version of every external tool
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
        # Approved versions other than the current one, loaded when a task asks: they
        # never change.
        self._older: dict[tuple[str, int], ExternalToolSpec] = {}


    def load(self) -> None:
        specs: dict[str, ToolSpec | ExternalToolSpec] = dict(discover("internal_tools"))

        for row in store.current_versions():
            try:
                spec = ExternalToolSpec.of(row, self.storage)

            except Exception as e:
                # One broken manifest must not take the other commands down.
                log.error("external tool %s v%s skipped: %s", row.name, row.version, e)
                continue

            specs[spec.name] = spec

        self._specs = specs
        # A version swept away since must not run from the cache.
        self._older = {}

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str, version: int | None = None) -> ToolSpec | ExternalToolSpec | None:
        """The tool; for an external one with `version`, that approved version."""
        spec = self._specs.get(name)

        # A built-in tool has no versions; an own command's current one is already here.
        if version is None or isinstance(spec, ToolSpec) or (spec is not None and spec.version == version):
            return spec

        if (name, version) not in self._older:
            row = store.approved_version(name, version)
            if row is None:
                return None

            self._older[(name, version)] = ExternalToolSpec.of(row, self.storage)

        return self._older[(name, version)]


    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[name].manifest() for name in sorted(self._specs)]


    def builtin_groups(self) -> set[str]:
        """Groups of the built-in tools: an own command takes a group of its own."""
        return {tool_group(name) for name, spec in self._specs.items() if isinstance(spec, ToolSpec)}
