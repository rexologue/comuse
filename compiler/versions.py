"""Which version of an external tool (ext.*) a task's steps run.

The model never chooses a version: a step gets the version its task already uses, or
the current one for a tool new to the task. Only the user moves a task to another
version (an explicit `tool_version` in their own edit, or POST …/tool-versions), and
that is a new task version to approve like any other. One task, one version per tool."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from common.schemas.task import tool_steps


def pin(data: dict[str, Any], catalog: dict[str, dict[str, Any]], *, keep: dict[str, int], explicit: bool) -> None:
    """Set `tool_version` on the external steps, in place: the version in `keep` (the task
    so far), else the current one. With `explicit` (the user's own edit) a version the
    step already names wins — the one that differs from the task's, wherever the step
    stands; the model's are overwritten, and a step it moved to a built-in tool loses its
    version."""
    chosen: dict[str, int] = {}

    if explicit:
        for step in tool_steps(data):
            tool, version = step["tool"], step.get("tool_version")
            if _own(tool, catalog, keep) and isinstance(version, int) and (tool not in chosen or version != keep.get(tool)):
                chosen[tool] = version

    for step in tool_steps(data):
        tool = step["tool"]

        if not _own(tool, catalog, keep):
            if not explicit:
                step.pop("tool_version", None)
            continue

        version = chosen.get(tool) or keep.get(tool) or (catalog.get(tool) or {}).get("version")
        if version is not None:
            step["tool_version"] = version


def _own(tool: str, catalog: dict[str, dict[str, Any]], keep: dict[str, int]) -> bool:
    """An own command (it has versions): so the catalog says, or the task already pins it."""
    return (catalog.get(tool) or {}).get("source") == "external" or tool in keep


async def overlay(
    catalog: dict[str, dict[str, Any]],
    wanted: dict[str, int],
    fetch: Callable[[str, int], Awaitable[dict[str, Any] | None]],
) -> dict[str, dict[str, Any]]:
    """The catalog with the manifests of the pinned versions in place of the current
    ones. A version that does not exist (any more) keeps the current manifest, so the
    check names the mismatch."""
    result = dict(catalog)

    for tool, version in wanted.items():
        if (catalog.get(tool) or {}).get("version") == version:
            continue

        manifest = await fetch(tool, version)
        if manifest is not None:
            result[tool] = manifest

    return result
