"""Tool framework: one command = one file with an `@tool`-decorated async function."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import typing
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict

from common.tools.context import NeedsInput, PermissionDenied, ToolContext, ToolError


__all__ = [
    "NeedsInput",
    "PermissionDenied",
    "ToolContext",
    "ToolError",
    "ToolInput",
    "ToolOutput",
    "ToolSpec",
    "discover",
    "function_name",
    "tool",
    "tool_name",
]


SideEffect = Literal["none", "read", "write", "external"]
DryRunPolicy = Literal["real", "draft", "skip"]


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
    side_effect: SideEffect
    capabilities: tuple[str, ...]
    secrets: tuple[str, ...]
    dry_run: DryRunPolicy
    timeout: int | None
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
            "side_effect": self.side_effect,
            "capabilities": list(self.capabilities),
            "secrets": list(self.secrets),
            "dry_run": self.dry_run,
            "timeout": self.timeout,
            "input_schema": self.input_model.model_json_schema(),
            # Outputs are dumped by field name (not alias), so describe them the same way.
            "output_schema": self.output_model.model_json_schema(by_alias=False),
            "source": "internal",
        }


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    title: str,
    card: str,
    description: str,
    side_effect: SideEffect = "none",
    capabilities: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
    dry_run: DryRunPolicy = "real",
    timeout: int | None = None,
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
            side_effect=side_effect,
            capabilities=tuple(capabilities),
            secrets=tuple(secrets),
            dry_run=dry_run,
            timeout=timeout,
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
