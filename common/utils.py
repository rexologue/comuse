from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def error_body(response: httpx.Response):
    """Return an httpx response body as parsed JSON if possible, else the raw text."""
    try:
        return response.json()

    except ValueError:
        return response.text


def new_id(prefix: str = "") -> str:
    """Short random id; the prefix makes ids readable in logs (`task_…`, `run_…`)."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def strip_think(text: str) -> str:
    """Remove `<think>…</think>` blocks that reasoning models put before the answer."""
    return _THINK_RE.sub("", text or "").strip()


def truncate(value: Any, limit: int = 4000) -> str:
    """Serialize a value for logs or LLM context and cut it to `limit` characters."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)

    if len(text) <= limit:
        return text

    return f"{text[:limit]}… [обрезано, всего {len(text)} символов]"


def shrink(value: Any, limit: int) -> str:
    """JSON of `value` within `limit` characters, every field kept: the longest strings
    are shortened (each says how much is left out), then the longest lists. A cut through
    the whole text would drop whatever comes after a long field — `truncated`, the list
    of attachments — and the model would not know they exist."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit or not isinstance(value, (dict, list)):
        return truncate(value, limit)

    value = json.loads(text)

    for _ in range(200):
        excess = len(text) - limit
        if excess <= 0:
            return text

        holder, key, item = _largest(value)
        if holder is None:
            break

        if isinstance(item, str):
            keep = max(100, len(item) - excess - 80)
            if keep >= len(item):
                break
            holder[key] = f"{item[:keep]}… [обрезано: показано {keep} из {len(item)} символов]"

        else:
            # In one cut, by the size of an item: a list cut again would count its own marker.
            size = len(json.dumps(item, ensure_ascii=False, default=str))
            keep = max(3, int(len(item) * max(0, size - excess - 80) / size))
            if keep >= len(item):
                break
            holder[key] = [*item[:keep], f"… [ещё {len(item) - keep} из {len(item)}]"]

        text = json.dumps(value, ensure_ascii=False, default=str)

    return truncate(text, limit)


def _largest(value: Any) -> tuple[Any, Any, Any]:
    """The longest string (over 100 characters) or the largest list (over 3 items)
    inside `value`, with what holds it."""
    best: tuple[int, Any, Any, Any] = (0, None, None, None)
    lists: tuple[int, Any, Any, Any] = (0, None, None, None)

    def walk(node: Any) -> None:
        nonlocal best, lists
        pairs = node.items() if isinstance(node, dict) else enumerate(node)
        for key, item in pairs:
            if isinstance(item, str) and len(item) > 100 and len(item) > best[0]:
                best = (len(item), node, key, item)
            elif isinstance(item, (dict, list)):
                if isinstance(item, list) and len(item) > 3:
                    size = len(json.dumps(item, ensure_ascii=False, default=str))
                    if size > lists[0]:
                        lists = (size, node, key, item)
                walk(item)

    walk(value)

    # Whichever frees more: one long text, or a list of many short items.
    string_saves = best[0] - 100 if best[1] is not None else 0
    list_saves = lists[0] * (1 - 3 / len(lists[3])) if lists[1] is not None else 0
    found = best if string_saves >= list_saves and best[1] is not None else lists

    return found[1], found[2], found[3]


def extract_json(text: str) -> Any:
    """Parse JSON from an LLM reply, tolerating ```json fences and surrounding prose."""
    cleaned = strip_think(text)

    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        return json.loads(cleaned)

    except ValueError:
        pass

    # Fall back to the outermost object/array in the text.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])

            except ValueError:
                continue

    raise ValueError("No JSON found in the model reply")


def strings_in(value: Any) -> list[str]:
    """Every string inside a JSON-like value: lists and dict values, nested ones too."""
    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        value = list(value.values())

    if isinstance(value, list):
        return [s for item in value for s in strings_in(item)]

    return []


def parse_number(value: Any) -> float | None:
    """A number as a table holds it: 1 234,5 → 1234.5; None for an empty cell. Raises
    ValueError for anything else."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise ValueError(f"{value!r} не число")

    if isinstance(value, (int, float)):
        return float(value)

    return float(str(value).replace(" ", "").replace("\u00a0", "").replace(",", "."))
