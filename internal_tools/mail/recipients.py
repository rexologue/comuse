"""Who a mail step may write to: exact addresses or `*@domain` masks."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field

from common.tools import ToolError


_MASK_RE = re.compile(r"^(?:[^@\s*<>,;]+|\*)@[^@\s*<>,;]+$")


def _check_mask(value: str) -> str:
    value = value.strip().lower()

    if not _MASK_RE.match(value):
        raise ValueError(f"{value!r}: нужен адрес или маска домена вида *@company.ru (просто «*» нельзя)")

    return value


RecipientMask = Annotated[str, AfterValidator(_check_mask), Field(description="Адрес (boss@company.ru) или все адреса домена (*@company.ru)")]


def check_allowed(recipients: list[str], allow_to: list[str]) -> None:
    """Every recipient must match `allow_to`; an empty list means no restriction."""
    if not allow_to:
        return

    exact = {mask for mask in allow_to if not mask.startswith("*@")}
    domains = {mask[2:] for mask in allow_to if mask.startswith("*@")}

    denied = [r for r in recipients if r.lower() not in exact and r.rsplit("@", 1)[-1].lower() not in domains]
    if denied:
        raise ToolError(f"Адресаты вне разрешённых ({', '.join(allow_to)}): {', '.join(denied)}")
