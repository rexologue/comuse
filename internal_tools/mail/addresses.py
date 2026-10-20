"""Address masks: an exact address or every address of a domain (`*@company.ru`)."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field


_MASK_RE = re.compile(r"^(?:[^@\s*<>,;]+|\*)@[^@\s*<>,;]+$")


def _check_mask(value: str) -> str:
    value = value.strip().lower()

    if not _MASK_RE.match(value):
        raise ValueError(f"{value!r}: нужен адрес или маска домена вида *@company.ru (просто «*» нельзя)")

    return value


AddressMask = Annotated[str, AfterValidator(_check_mask), Field(description="Адрес (boss@company.ru) или все адреса домена (*@company.ru)")]


def matches(address: str, masks: list[str]) -> bool:
    """Exact comparison of a bare address (not a substring of a header)."""
    address = address.strip().lower()

    return any(address == mask or (mask.startswith("*@") and address.rsplit("@", 1)[-1] == mask[2:]) for mask in masks)
