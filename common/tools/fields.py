"""Field types shared by tool inputs."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field


_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+$")


def _check_email(value: str) -> str:
    """Shape only: one `@`, no spaces or separators. Deliverability is the mail
    server's call; who may get mail is the approved task or the user's confirmation in
    chat. Stricter validators reject domains like `company.local`."""
    value = value.strip()

    if not _EMAIL_RE.match(value):
        raise ValueError(f"не похоже на адрес почты: {value!r}")

    return value


# Marks an argument whose values are workspace paths. The executor checks every such
# value before the tool runs (inside the workspace or data:templates / data:outputs),
# whatever the tool then does with it.
PATH_ARG = "x-path"

WorkspacePath = Annotated[str, Field(json_schema_extra={PATH_ARG: "path"})]


EmailAddress = Annotated[str, AfterValidator(_check_email), Field(description="Адрес почты, например ivanov@company.ru")]
