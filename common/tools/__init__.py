"""Tool framework: one command = one file with an `@tool`-decorated async function."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict

from common.tools.context import Credential, PermissionDenied, ToolContext, ToolError
from common.tools.domain import DomainSettings, domain_settings, settings_schema
from common.tools.fields import PATH_ARG
from common.tools.guides import guide_of
from common.tools.refs import file_refs


__all__ = [
    "Credential",
    "DomainSettings",
    "PermissionDenied",
    "ToolContext",
    "ToolError",
    "ToolInput",
    "ToolOutput",
    "ToolSpec",
    "discover",
    "domain_settings",
    "function_name",
    "path_refs",
    "tool",
    "tool_name",
]


# Whether the chat asks the user before a call: True — yes (the user may let the chat stop
# asking for this command), "always" — every time, False — never (it only looks or
# computes; files it creates go to the run's own outputs). A command that asks must
# also handle ctx.dry_run itself: whatever is worth a question must not happen in a dry run.
Confirm = bool | Literal["always"]


class ToolInput(BaseModel):
    """Base for tool inputs; unknown arguments are an error, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class ToolOutput(BaseModel):
    pass


@dataclass(slots=True, frozen=True)
class ToolSpec:
    name: str
    title: str
    card: str
    description: str
    confirm: Confirm
    # Arguments naming a stored account: the executor loads it (config + password from
    # the keyring) and hands it over as ctx.credential(<argument>).
    secrets: tuple[str, ...]
    # Arguments typed as workspace paths: {name: "path" | "refs"} (see common/tools/fields.py).
    path_args: dict[str, str]
    timeout: int | None
    # Always offered to the chat agent, whatever the request (looking around starts there).
    core: bool
    input_model: type[ToolInput]
    output_model: type[BaseModel]
    fn: Callable[[Any, ToolContext], Awaitable[Any]]

    def manifest(self) -> dict[str, Any]:
        """JSON description shared with agent-loop and compiler (no code, no callables)."""
        return {
            "name": self.name,
            "function_name": function_name(self.name),
            "title": self.title,
            "card": self.card,
            "description": self.description,
            "confirm": self.confirm,
            "secrets": list(self.secrets),
            "timeout": self.timeout,
            "input_schema": self.input_model.model_json_schema(),
            # Outputs are dumped by field name (not alias), so describe them the same way.
            "output_schema": self.output_model.model_json_schema(by_alias=False),
            # Settings of the tool's domain a task using it gets (None: the domain has none).
            "settings_schema": settings_schema(self.name),
            # How to use the tool's group, for the agent and the compiler (None: no guide).
            "guide": guide_of(self.name),
            "core": self.core,
            "source": "internal",
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    title: str,
    card: str,
    description: str,
    confirm: Confirm = True,
    secrets: tuple[str, ...] = (),
    timeout: int | None = None,
    core: bool = False,
):
    """Register a tool. The function's first parameter annotation is its input model,
    its return annotation the output model; both become JSON schemas for the LLM."""

    def decorator(fn):
        hints = typing.get_type_hints(fn)
        params = list(inspect.signature(fn).parameters)

        if len(params) != 2:
            raise TypeError(f"tool {name}: expected signature (inp, ctx), got {params}")

        spec = ToolSpec(
            name=name,
            title=title,
            card=card,
            description=description,
            confirm=confirm,
            secrets=tuple(secrets),
            path_args=_path_args(hints[params[0]]),
            timeout=timeout,
            core=core,
            input_model=hints[params[0]],
            output_model=hints["return"],
            fn=fn,
        )

        if name in _REGISTRY and _REGISTRY[name].fn is not fn:
            raise ValueError(f"tool {name} is registered twice")

        _REGISTRY[name] = spec
        fn.tool_spec = spec

        return fn

    return decorator


def path_refs(spec: ToolSpec, inp: BaseModel) -> list[str]:
    """Every path an input names through its path-typed arguments."""
    refs: list[str] = []

    for name, kind in spec.path_args.items():
        value = getattr(inp, name, None)
        refs += file_refs(value) if kind == "refs" else _strings(value)

    return refs


def _path_args(model: type[BaseModel]) -> dict[str, str]:
    marks = {}

    for name, prop in model.model_json_schema().get("properties", {}).items():
        mark = _find_mark(prop)
        if mark:
            marks[name] = mark

    return marks


def _find_mark(node: Any) -> str | None:
    if isinstance(node, dict):
        if PATH_ARG in node:
            return node[PATH_ARG]

        node = list(node.values())

    if isinstance(node, list):
        return next((mark for item in node if (mark := _find_mark(item))), None)

    return None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        value = list(value.values())

    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]

    return []


def discover(package_name: str = "internal_tools") -> dict[str, ToolSpec]:
    """Import every module of the package so their `@tool` decorators register."""
    package = importlib.import_module(package_name)

    for module in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        importlib.import_module(module.name)

    return dict(_REGISTRY)


def function_name(name: str) -> str:
    """OpenAI function names allow only [a-zA-Z0-9_-], so `mail.send` → `mail__send`."""
    return name.replace(".", "__")


def tool_name(fn_name: str) -> str:
    return fn_name.replace("__", ".")
