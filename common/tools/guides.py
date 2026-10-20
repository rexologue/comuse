"""Group guides: how to use a tool group well, written by the group itself.

The chat agent's prompt and the compiler's rules hold only what is theirs (working
through tools, dates, paths, the TaskSpec format); what is specific to mail, files or
reports comes from the group's guide. Guides travel in the tool manifests, as domain
settings do, and nothing outside the group names its tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.tools.domain import tool_domain


@dataclass(slots=True, frozen=True)
class Guide:
    # For the chat agent: how to do things with the group's tools.
    agent: str = ""
    # For the compiler: how a task uses the group's tools.
    compiler: str = ""


_GUIDES: dict[str, Guide] = {}


def domain_guide(domain: str, *, agent: str = "", compiler: str = "") -> Guide:
    guide = Guide(agent=agent.strip(), compiler=compiler.strip())

    if domain in _GUIDES and _GUIDES[domain] != guide:
        raise ValueError(f"guide of domain {domain} is registered twice")

    _GUIDES[domain] = guide

    return guide


def guide_of(tool: str) -> dict[str, Any] | None:
    guide = _GUIDES.get(tool_domain(tool))
    return {"agent": guide.agent, "compiler": guide.compiler} if guide else None


def collect(manifests: list[dict[str, Any]], role: str) -> list[str]:
    """One text per group among the manifests, in a stable order."""
    texts: dict[str, str] = {}

    for manifest in sorted(manifests, key=lambda m: m["name"]):
        text = (manifest.get("guide") or {}).get(role)
        if text:
            texts.setdefault(tool_domain(manifest["name"]), text)

    return list(texts.values())
