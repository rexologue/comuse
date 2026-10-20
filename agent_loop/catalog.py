"""Which tools the model sees on a turn. Showing all of them hurts small local
models, so the catalog keeps the top-k by embedding similarity to the request."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from common.clients.llm_client import LLMClient
from common.clients.main_client import MainClient
from common.errors import ApiError


log = logging.getLogger("catalog")

_CATALOG_TTL_SEC = 60

# After an embeddings failure, ranking is skipped for a while instead of paying the
# connection retries on every chat turn.
_EMBED_BACKOFF_SEC = 300


class ToolCatalog:
    def __init__(self, main: MainClient, llm: LLMClient, embed_model: str, top_k: int):

        self.main        = main
        self.llm         = llm
        self.embed_model = embed_model
        self.top_k       = top_k

        self._manifests: dict[str, dict[str, Any]] = {}
        self._loaded_at = 0.0
        self._vectors: dict[str, list[float]] = {}
        self._embed_down_until = 0.0


    async def manifests(self) -> dict[str, dict[str, Any]]:
        if not self._manifests or time.monotonic() - self._loaded_at > _CATALOG_TTL_SEC:
            data = await self.main.get("/tools")
            self._manifests = {tool["name"]: tool for tool in data["tools"]}
            self._loaded_at = time.monotonic()

        return self._manifests


    async def select(self, query: str, always: set[str] | None = None) -> list[dict[str, Any]]:
        """Top-k tools for the query plus `always` (tools already used in the session)."""
        manifests = await self.manifests()
        # Tools marked core (looking around is how every request starts) are never ranked out.
        core = {name for name, manifest in manifests.items() if manifest.get("core")}
        always = {name for name in (always or set()) | core if name in manifests}

        # Without an embeddings model (or with few tools) the model simply sees them all.
        if not self.embed_model or len(manifests) <= self.top_k or time.monotonic() < self._embed_down_until:
            return list(manifests.values())

        try:
            ranked = await self._rank(query, manifests)

        except ApiError as e:
            log.warning("embedding ranking failed, exposing all tools for %ds: %s", _EMBED_BACKOFF_SEC, e.message)
            self.mark_embeddings_down()
            return list(manifests.values())

        chosen = list(always)
        for name in ranked:
            if len(chosen) >= self.top_k + len(always):
                break

            if name not in chosen:
                chosen.append(name)

        return [manifests[name] for name in chosen]


    def mark_embeddings_down(self) -> None:
        self._embed_down_until = time.monotonic() + _EMBED_BACKOFF_SEC


    async def _rank(self, query: str, manifests: dict[str, dict[str, Any]]) -> list[str]:
        missing = [name for name in manifests if name not in self._vectors]
        if missing:
            texts = [_tool_text(manifests[name]) for name in missing]
            vectors = await self.llm.embed(texts, model=self.embed_model)
            self._vectors.update(zip(missing, vectors))

        [query_vector] = await self.llm.embed([query], model=self.embed_model)

        scores = {name: _cosine(query_vector, self._vectors[name]) for name in manifests}

        return sorted(scores, key=scores.get, reverse=True)


def to_function(manifest: dict[str, Any]) -> dict[str, Any]:
    """OpenAI `tools` entry for an executor tool."""
    return {
        "type": "function",
        "function": {
            "name": manifest["function_name"],
            "description": f"{manifest['title']}. {manifest['description']}",
            "parameters": manifest["input_schema"],
        },
    }


def _tool_text(manifest: dict[str, Any]) -> str:
    return f"{manifest['name']}: {manifest['title']}. {manifest['description']}"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))

    return dot / norm if norm else 0.0
