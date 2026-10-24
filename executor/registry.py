"""Tool catalog: internal_tools/ plus the current version of every external tool
(common/external_tools); others read it over HTTP (GET /tools)."""

from __future__ import annotations

import logging
from typing import Any

from common.external_tools import store
from common.external_tools.spec import ExternalToolSpec
from common.settings import StorageSettings
from common.tools import ToolSpec, discover


log = logging.getLogger("registry")


class ToolRegistry:
    def __init__(self, storage: StorageSettings):
        self.storage = storage
        self._specs: dict[str, ToolSpec | ExternalToolSpec] = {}


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


    def get(self, name: str) -> ToolSpec | ExternalToolSpec | None:
        return self._specs.get(name)


    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[name].manifest() for name in sorted(self._specs)]
