"""Tool catalog: internal_tools/ plus the current version of every external tool
(common/external_tools); others read it over HTTP (GET /tools)."""

from __future__ import annotations

import logging
from typing import Any

from common.external_tools import PREFIX, is_external, store
from common.external_tools.spec import ExternalToolSpec
from common.settings import StorageSettings
from common.tools import ToolSpec, discover


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

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str, version: int | None = None) -> ToolSpec | ExternalToolSpec | None:
        """The tool; for an external one with `version`, that approved version."""
        spec = self._specs.get(name)

        if version is None or not is_external(name) or (spec is not None and spec.version == version):
            return spec

        if (name, version) not in self._older:
            row = store.approved_version(name.removeprefix(PREFIX), version)
            if row is None:
                return None

            self._older[(name, version)] = ExternalToolSpec.of(row, self.storage)

        return self._older[(name, version)]


    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[name].manifest() for name in sorted(self._specs)]
