"""Collect text for llm.* tools from whatever the previous step produced:
plain text, a file path, a fs.list result, a list of any of those."""

from __future__ import annotations

import asyncio
from typing import Any

from common.tools import PermissionDenied, ToolContext
from common.tools.refs import file_refs
from common.utils import truncate
from internal_tools.docs.readers import read_document


MAX_INPUT_CHARS = 60000


async def gather_text(value: Any, ctx: ToolContext, limit: int = MAX_INPUT_CHARS) -> str:
    parts: list[str] = []
    await _collect(value, ctx, parts)

    return truncate("\n\n".join(p for p in parts if p), limit)


async def _collect(value: Any, ctx: ToolContext, parts: list[str]) -> None:
    if value is None:
        return

    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and len(value) <= 6:
            await _collect_path(value["path"], ctx, parts)
            return

        if isinstance(value.get("files"), list):
            for item in value["files"]:
                await _collect(item, ctx, parts)
            return

        parts.append(truncate(value, MAX_INPUT_CHARS))
        return

    if isinstance(value, list):
        for item in value:
            await _collect(item, ctx, parts)
        return

    text = str(value)
    refs = file_refs(text)

    # A string naming one or more files reads those files; anything else is text.
    if refs and all(_looks_like_path(ref) for ref in refs):
        collected = [await _collect_path(ref, ctx, parts) for ref in refs]
        if all(collected):
            return

    parts.append(text)


async def _collect_path(ref: str, ctx: ToolContext, parts: list[str]) -> bool:
    try:
        path = ctx.read_path(ref)

    except PermissionDenied:
        raise

    except Exception:
        return False

    if not path.is_file():
        return False

    text = await asyncio.to_thread(read_document, path)
    parts.append(f"### Файл {ctx.to_ref(path)}\n\n{text}")

    return True


def _looks_like_path(text: str) -> bool:
    return "\n" not in text and len(text) < 300 and ("/" in text or "." in text[-6:])
