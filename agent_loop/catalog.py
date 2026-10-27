"""Which tools the model sees on a turn. Showing all of them hurts small local
models, so the catalog keeps the top-k by embedding similarity to the request."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from common import tunables
from common.clients.catalog import CatalogCache
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.llm import Role


log = logging.getLogger("catalog")

# After an embeddings failure, ranking is skipped for a while instead of paying the
# connection retries on every chat turn.
_EMBED_BACKOFF_SEC = 300


class ToolCatalog:
    def __init__(self, main: MainClient, embed: Role):

        self.main  = main
        self.embed = embed

        self._catalog = CatalogCache(main)
        # By the hash of the tool's definition: a changed description is embedded anew.
        self._vectors: dict[str, list[float]] = {}
        # Which embeddings endpoint made the vectors: another model's are not comparable.
        self._vectors_by: tuple | None = None
        self._embed_down_until = 0.0


    async def manifests(self, *, fresh: bool = False) -> dict[str, dict[str, Any]]:
        return await self._catalog.get(fresh=fresh)


    async def select(self, query: str, always: set[str] | None = None) -> list[dict[str, Any]]:
        """Top-k tools for the query plus `always` (tools already used in the session).
        Every turn asks for the tools anew: a command approved a moment ago is offered."""
        manifests = await self.manifests(fresh=True)
        # Tools marked core (looking around is how every request starts) are never ranked out.
        core = {name for name, manifest in manifests.items() if manifest.get("core")}
        always = {name for name in (always or set()) | core if name in manifests}
        top_k = tunables.get("agent_tools_top_k")

        # Without an embeddings model (or with few tools) the model simply sees them all.
        endpoint = self.embed.endpoint()
        if endpoint is None or len(manifests) <= top_k or time.monotonic() < self._embed_down_until:
            return list(manifests.values())

        endpoint_id = (endpoint.provider, endpoint.version, endpoint.base_url, endpoint.model)
        if endpoint_id != self._vectors_by:
            self._vectors, self._vectors_by = {}, endpoint_id

        try:
            ranked = await self._rank(query, manifests)

        except ApiError as e:
            log.warning("embedding ranking failed, exposing all tools for %ds: %s", _EMBED_BACKOFF_SEC, e.message)
            self.mark_embeddings_down()
            return list(manifests.values())

        chosen = list(always)
        for name in ranked:
            if len(chosen) >= top_k + len(always):
                break

            if name not in chosen:
                chosen.append(name)

        return [manifests[name] for name in chosen]


    def mark_embeddings_down(self) -> None:
        self._embed_down_until = time.monotonic() + _EMBED_BACKOFF_SEC


    async def _rank(self, query: str, manifests: dict[str, dict[str, Any]]) -> list[str]:
        missing = [manifest for manifest in manifests.values() if manifest["hash"] not in self._vectors]
        if missing:
            vectors = await self.embed.embed([_tool_text(manifest) for manifest in missing])
            self._vectors.update(zip((manifest["hash"] for manifest in missing), vectors))

        [query_vector] = await self.embed.embed([query])

        scores = {name: _cosine(query_vector, self._vectors[manifest["hash"]]) for name, manifest in manifests.items()}

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
