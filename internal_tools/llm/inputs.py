"""What llm.* tools give the model: text, or a previous step's result as it is. Reading a
file is not theirs: a document comes as the text of the step that read it."""

from __future__ import annotations

from typing import Any

from common.tools import PermissionDenied, ToolContext, ToolError
from common.utils import truncate


MAX_INPUT_CHARS = 60000


def gather_text(value: Any, ctx: ToolContext, limit: int = MAX_INPUT_CHARS) -> str:
    if value is None:
        return ""

    _refuse_paths(value, ctx)

    return truncate(value, limit)


def _refuse_paths(value: Any, ctx: ToolContext) -> None:
    """A path, a list of paths or a file listing would reach the model as file names,
    not their text: say so instead of answering about a name."""
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        candidates = value
    else:
        # A listing: items with a `path` and no text of their own.
        candidates = [item["path"] for item in _items(value) if isinstance(item, dict) and isinstance(item.get("path"), str) and "text" not in item]

    for candidate in candidates:
        if "\n" in candidate or len(candidate) > 300:
            continue

        try:
            is_file = ctx.read_path(candidate.strip()).is_file()

        except (PermissionDenied, OSError, ValueError):
            continue

        if is_file:
            raise ToolError(
                f"«{candidate}» — путь к файлу, а нужен его текст: сначала прочитай документ шагом чтения "
                "документов и передай сюда его text"
            )


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        return [item for inner in value.values() if isinstance(inner, list) for item in inner]

    return []
