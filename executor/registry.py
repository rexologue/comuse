"""Tool catalog: discovered from internal_tools/; others read it over HTTP (GET /tools)."""

from __future__ import annotations

import logging
from typing import Any

from common.tools import ToolSpec, discover


log = logging.getLogger("registry")


class ToolRegistry:
    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}


    def load(self) -> None:
        self._specs = discover("internal_tools")

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)


    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[name].manifest() for name in sorted(self._specs)]

