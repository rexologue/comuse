"""Settings the user tunes in a task, each at three levels:

- the default: the tool declares its settings (`@tool(settings=Model)`), flat plain
  values, each with a default;
- for the whole task: one value for every step that runs the tool;
- for one step: a value for that step only (the step's id).

A task keeps only what the user changed, by group and tool (the group is the name
before the dot):

    {"mail": {"mail.send": {"send_directly": {"all": true, "steps": {"to_client": false}}}}}

A step's value is its own, else the task's, else the default. Nothing here knows what a
field means: the tool that declared it reads it (`ctx.tool_settings`)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


_SCALARS = {"boolean", "integer", "number", "string"}


class ToolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


def tool_group(tool: str) -> str:
    """`mail.send` → `mail`."""
    return tool.split(".", 1)[0]


def check_settings(tool: str, model: type[ToolSettings]) -> None:
    """Plain values, each with a default: a form shows them, and a step that nobody
    tuned runs on the defaults."""
    for name, prop in model.model_json_schema()["properties"].items():
        if prop.get("type") not in _SCALARS and "enum" not in prop:
            raise TypeError(f"{tool} settings: `{name}` must be a plain value (bool, number, string, choice)")

        if model.model_fields[name].is_required():
            raise TypeError(f"{tool} settings: `{name}` needs a default")


def changed(task_settings: dict[str, Any], tool: str) -> dict[str, dict[str, Any]]:
    """{field: {"all"?: value, "steps"?: {step: value}}} the user changed for this tool."""
    return ((task_settings or {}).get(tool_group(tool)) or {}).get(tool) or {}


def resolve(task_settings: dict[str, Any], tool: str, step: str) -> dict[str, Any]:
    """The values the user set for this step of this tool: its own, else the task's.
    What is not here takes the tool's default."""
    values: dict[str, Any] = {}

    for field, levels in changed(task_settings, tool).items():
        if step in (levels.get("steps") or {}):
            values[field] = levels["steps"][step]
        elif "all" in levels:
            values[field] = levels["all"]

    return values
