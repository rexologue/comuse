"""Path references used in task specs (shared by the compiler and the executor)."""

from __future__ import annotations

from pathlib import PurePosixPath


# `data:templates/…` and `data:outputs/…` point into DATA_DIR; anything else is
# relative to WORKSPACE_DIR.
DATA_PREFIX = "data:"


def normalize_folder(folder: str) -> str:
    """`./Отчёты/`, `Отчёты\\` and `Отчёты` all mean the same workspace folder."""
    cleaned = folder.strip().replace("\\", "/").strip("/")

    if cleaned in ("", "."):
        return ""

    return PurePosixPath(cleaned).as_posix()
