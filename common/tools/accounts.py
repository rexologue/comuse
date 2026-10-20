"""Account kinds: what a tool group needs to connect somewhere (mail: IMAP/SMTP).

The executor stores an account as a name, a kind, non-secret settings and a password
in the keyring; everything that depends on the kind (presets, which settings are
required, how to try a login) is declared by the group that owns the kind."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True, frozen=True)
class AccountKind:
    name: str
    title: str
    # Settings for known providers, merged under the user's own config.
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Completes and checks the settings; raises ValueError with a message for the user.
    prepare: Callable[[dict[str, Any]], dict[str, Any]] = lambda config: config
    # Tries to log in (blocking); raises on failure.
    check: Callable[[dict[str, Any], str], None] | None = None


_KINDS: dict[str, AccountKind] = {}


def account_kind(kind: AccountKind) -> AccountKind:
    if kind.name in _KINDS and _KINDS[kind.name] is not kind:
        raise ValueError(f"account kind {kind.name} is registered twice")

    _KINDS[kind.name] = kind

    return kind


def get_account_kind(name: str) -> AccountKind | None:
    return _KINDS.get(name)


def account_kinds() -> dict[str, AccountKind]:
    return dict(_KINDS)
