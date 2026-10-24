"""Folders of DATA_DIR that tools see as `data:<area>/…`: outputs, which every run and
chat gets, and the ones a tool group declares for files it keeps between runs (the
docs group: templates). The executor opens these and nothing else of DATA_DIR."""

from __future__ import annotations


OUTPUTS = "outputs"

_AREAS: dict[str, str] = {OUTPUTS: "результаты запусков и чатов"}


def data_area(name: str, holds: str) -> None:
    """A group's own folder in DATA_DIR; `holds` says what is in it."""
    _AREAS[name] = holds


def data_areas() -> dict[str, str]:
    return dict(_AREAS)
