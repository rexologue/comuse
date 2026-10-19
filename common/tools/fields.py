"""Field types shared by tool inputs."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field


_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+$")


def _check_email(value: str) -> str:
    """Shape only: one `@`, no spaces or separators. Deliverability is the mail
    server's call; task permissions and user confirmation decide who may get mail.
    Stricter validators reject internal domains such as `company.local`."""
    value = value.strip()

    if not _EMAIL_RE.match(value):
        raise ValueError(f"не похоже на адрес почты: {value!r}")

    return value


EmailAddress = Annotated[str, AfterValidator(_check_email), Field(description="Адрес почты, например ivanov@company.ru")]
