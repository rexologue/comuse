"""Inputs that name files: a path, a list of paths, or a previous step's result whose
items carry a `path` (a file listing, say: whichever tool made it). A loose `Any` gives
the model no schema at all, and it then sends JSON-encoded arrays or comma-joined paths
as one string."""

from __future__ import annotations

import json
from typing import Annotated, Any, Union

from pydantic import Field

from common.tools.fields import PATH_ARG


_SHAPES = Union[str, list[Union[str, dict[str, Any]]], dict[str, Any]]

# Only files: the executor checks every path in it before the tool runs.
FileRefs = Annotated[
    _SHAPES,
    Field(
        description="Файлы: один путь, список путей ([\"a.xlsx\", \"b.xlsx\"]) или результат шага со списком файлов целиком",
        json_schema_extra={PATH_ARG: "refs"},
    ),
]


def file_refs(value: Any) -> list[str]:
    """Flatten any accepted shape into a list of path references."""
    if isinstance(value, str):
        return _split_string(value)

    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            return [value["path"]]

        # A result that holds its files somewhere inside: only nested lists and objects,
        # a plain text field (a name, a status) is not a path.
        return [ref for item in value.values() if isinstance(item, (list, dict)) for ref in file_refs(item)]

    if isinstance(value, list):
        return [ref for item in value for ref in file_refs(item)]

    return []


def _split_string(text: str) -> list[str]:
    stripped = text.strip()

    # A JSON array sent as a string.
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            return file_refs(json.loads(stripped))

        except ValueError:
            pass

    # Several paths joined with commas or new lines; a single path may itself contain
    # a comma, so split only when every part looks like a file name.
    for separator in ("\n", ","):
        parts = [p.strip() for p in stripped.split(separator) if p.strip()]
        if len(parts) > 1 and all("." in p.rsplit("/", 1)[-1] for p in parts):
            return parts

    return [stripped] if stripped else []
