"""Tool catalog: discovered from internal_tools/ and mirrored into the `tools` table."""

from __future__ import annotations

import logging
from typing import Any

from common.storage.db import session
from common.storage.models import Tool
from common.tools import ToolSpec, discover


log = logging.getLogger("registry")


class ToolRegistry:
    def __init__(self):
        self._specs: dict[str, ToolSpec] = {}


    def load(self) -> None:
        self._specs = discover("internal_tools")
        self._sync_table()

        log.info("tools loaded: %d (%s)", len(self._specs), ", ".join(sorted(self._specs)))


    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)


    def catalog(self) -> list[dict[str, Any]]:
        return [self._specs[name].manifest() for name in sorted(self._specs)]


    def _sync_table(self) -> None:
        """executor is the only writer of `tools`: upsert current, disable vanished."""
        with session() as db:
            existing = {row.name: row for row in db.query(Tool).filter(Tool.source == "internal")}

            for name, spec in self._specs.items():
                row = existing.pop(name, None) or Tool(name=name, source="internal")
                row.manifest = spec.manifest()
                row.enabled = True
                db.add(row)

            for row in existing.values():
                row.enabled = False

            db.commit()
