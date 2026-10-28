"""A label of a letter: an IMAP keyword (Thunderbird shows them as tags, Gmail as
labels). The task's own mark of what it has handled, instead of the user's read state."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field


# An IMAP atom, and nothing a server reserves (\Seen, $Junk): Latin letters, digits, _ . -
# In the schema, not only in a check here: the compiler validates a step's arguments by
# it, and the model reads it.
Label = Annotated[
    str,
    Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$",
        description="Метка письма: латиница, цифры, _ . - (до 40 знаков), по делу задачи: invoices_forwarded, reports_archived",
    ),
]
