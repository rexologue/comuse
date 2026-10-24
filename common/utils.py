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
