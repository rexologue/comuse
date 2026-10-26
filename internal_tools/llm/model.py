"""The model the llm.* group asks: the instruct role (common/llm.py). Only this group
talks to the model; any other tool that needs one gets it as an llm.* step before it."""

from __future__ import annotations

from common.llm import LLM, Role
from common.tools import ToolContext


_llm: LLM | None = None


def instruct(ctx: ToolContext) -> Role:
    """Resolved on every call (common/llm.py), so a provider changed in settings applies."""
    global _llm

    if _llm is None:
        _llm = LLM(ctx.settings)

    return _llm.instruct
