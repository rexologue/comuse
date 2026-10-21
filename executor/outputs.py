"""data:outputs/<run or chat>/ — the files tools make. The executor writes them, so it
sweeps them: a folder nothing in has changed for `retention_days` goes."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from common.retention import cutoff
from common.settings import StorageSettings


def sweep(storage: StorageSettings) -> int:
    if not storage.outputs_dir.is_dir():
        return 0

    limit = cutoff()
    removed = 0

    for folder in storage.outputs_dir.iterdir():
        if folder.is_dir() and _last_change(folder) < limit:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1

    return removed


def _last_change(folder: Path) -> datetime:
    stamps = [folder.stat().st_mtime, *(path.stat().st_mtime for path in folder.rglob("*"))]

    return datetime.fromtimestamp(max(stamps), tz=timezone.utc)
