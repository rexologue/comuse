"""Account kinds: what a tool group needs to connect somewhere (mail: IMAP/SMTP).

The executor stores an account as a name, a kind, non-secret settings and a password
in the keyring; everything that depends on the kind (its settings and how a form asks
for them, presets, how to try a login) is declared by the group that owns the kind: a
built-in group in its code, a plugin in its manifest (registered while it is approved,
`owner` its name)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True, frozen=True)
class AccountField:
    """One non-secret setting of a kind, as a form asks for it."""
    name: str
    title: str
    type: str = "text"           # text | number | choice
    required: bool = False
    choices: tuple[str, ...] = ()
    hint: str = ""
    # What an empty value means, for the user ("ssl", "993 for ssl, else 143").
    default: str = ""

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "type": self.type,
            "required": self.required,
            "choices": list(self.choices),
            "hint": self.hint,
            "default": self.default,
        }


@dataclass(slots=True, frozen=True)
class AccountKind:
    name: str
    title: str
    # The settings in `config`, for a form; presets fill some of them.
    fields: tuple[AccountField, ...] = ()
    # What the secret is called to the user (an app password, a token).
    secret_title: str = "Пароль"
    # Settings for known providers, merged under the user's own config.
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Completes and checks the settings; raises ValueError with a message for the user.
    prepare: Callable[[dict[str, Any]], dict[str, Any]] = lambda config: config
    # Tries to log in (blocking); raises on failure.
    check: Callable[[dict[str, Any], str], None] | None = None
    # The plugin that declared it; None — a built-in group.
    owner: str | None = None


_KINDS: dict[str, AccountKind] = {}


def account_kind(kind: AccountKind) -> AccountKind:
    """Register a kind; a plugin's own kind replaces what it registered before."""
    known = _KINDS.get(kind.name)
    if known is not None and known is not kind and (known.owner is None or known.owner != kind.owner):
        raise ValueError(f"account kind {kind.name} is registered twice")

    _KINDS[kind.name] = kind

    return kind


def drop_account_kind(name: str, owner: str) -> None:
    """A plugin's kind, when the plugin goes; accounts of it stay, unusable till it is back."""
    if name in _KINDS and _KINDS[name].owner == owner:
        del _KINDS[name]


def get_account_kind(name: str) -> AccountKind | None:
    return _KINDS.get(name)


def account_kinds() -> dict[str, AccountKind]:
    return dict(_KINDS)
