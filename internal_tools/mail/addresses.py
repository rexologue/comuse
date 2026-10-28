"""Address masks: an exact address or every address of a domain (`*@company.ru`).

They select by the From header, which a sender can forge: a filter, not proof of who
wrote. Replies are safe (they go to that address), anything else done with the letter is
not; checking the server's Authentication-Results is left for a real mail server."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field


_MASK_RE = re.compile(r"^(?:[^@\s*<>,;]+|\*)@[^@\s*<>,;]+$")


def check_mask(value: str) -> str:
    value = value.strip().lower()

    if not _MASK_RE.match(value):
        raise ValueError(f"{value!r}: нужен адрес или маска домена вида *@company.ru (просто «*» нельзя)")

    return value


AddressMask = Annotated[str, AfterValidator(check_mask), Field(description="Адрес (boss@company.ru) или все адреса домена (*@company.ru)")]


_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+$")


def _check_email(value: str) -> str:
    """Shape only: one `@`, no spaces or separators. Deliverability is the mail
    server's call; who may get mail is the approved task or the user's confirmation in
    chat. Stricter validators reject domains like `company.local`."""
    value = value.strip()

    if not _EMAIL_RE.match(value):
        raise ValueError(f"не похоже на адрес почты: {value!r}")

    return value


EmailAddress = Annotated[str, AfterValidator(_check_email), Field(description="Адрес почты, например ivanov@company.ru")]


def matches(address: str, masks: list[str]) -> bool:
    """Exact comparison of a bare address (not a substring of a header)."""
    address = address.strip().lower()

    return any(address == mask or (mask.startswith("*@") and address.rsplit("@", 1)[-1] == mask[2:]) for mask in masks)
