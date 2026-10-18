"""Inputs that name files: a path, a list of paths, or a previous step's result
(`fs.list` output, `{path: …}` items). A loose `Any` gives the model no schema at
all, and it then sends JSON-encoded arrays or comma-joined paths as one string."""

from __future__ import annotations

import json
from typing import Annotated, Any, Union

from pydantic import Field


FileRefs = Annotated[
    Union[str, list[Union[str, dict[str, Any]]], dict[str, Any]],
    Field(
        description=(
            "Файлы: один путь, список путей ([\"a.xlsx\", \"b.xlsx\"]) или результат fs.list целиком"
        )
    ),
]


def file_refs(value: Any) -> list[str]:
    """Flatten any accepted shape into a list of path references."""
    if isinstance(value, str):
        return _split_string(value)

    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            return [value["path"]]

        return file_refs(value.get("files", []))

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
