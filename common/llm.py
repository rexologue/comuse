"""Which model serves each role:

- chat — the agent in the chat and the compiler (multi-step work, calls tools);
- instruct — llm.* tools and template markup (single structured calls); the chat
  model when no other is set;
- embed — ranking tools for a request; none = the agent sees every tool.

A provider is one model at an OpenAI-compatible server (`llm_providers`); a role points
at any of them by id from the `settings` table. main writes both through /llm, and
registers the models of LLM_* variables as providers on start (main/providers.py).
Timeouts, retries, proxy and reasoning effort are settings (common/tunables.py).

A role is resolved on every call, so a change applies without a restart. Clients are
kept per provider version and transport settings: a changed provider gets a new client,
and its key is read from the store only then."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common import tunables
from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.secrets import SecretStore
from common.settings import Settings
from common.storage.db import session
from common.storage.models import LLMProvider, Setting


ROLES = ("chat", "instruct", "embed")


def role_key(role: str) -> str:
    """The `settings` row that names the role's provider."""
    return f"llm_{role}"


class LLMNotConfigured(ApiError):
    def __init__(self, role: str):
        super().__init__(
            status_code=503,
            message=(
                f"Модель для роли {role} не назначена: добавьте провайдера (POST /llm/providers) "
                f"и назначьте его на роль (PATCH /llm)"
            ),
        )


@dataclass(slots=True, frozen=True)
class Endpoint:
    provider: str
    # Changes with every change of the provider: part of the client cache key.
    version: str
    base_url: str
    model: str
    has_key: bool
    reasoning_effort: str | None = None


def assigned(role: str) -> str | None:
    """Id of the provider set for the role; instruct falls back to chat."""
    with session() as db:
        rows = {row.key: row.value for row in db.query(Setting).filter(Setting.key.in_([role_key(r) for r in ROLES]))}

    provider_id = rows.get(role_key(role))
    if provider_id is None and role == "instruct":
        provider_id = rows.get(role_key("chat"))

    return provider_id


def resolve(role: str) -> Endpoint | None:
    """The endpoint serving `role` now; None when no provider is set for it."""
    provider_id = assigned(role)
    if provider_id is None:
        return None

    with session() as db:
        provider = db.get(LLMProvider, provider_id)

    if provider is None:
        return None

    return Endpoint(
        provider=provider.id,
        version=provider.updated_at.isoformat(),
        base_url=provider.base_url,
        model=provider.model,
        has_key=provider.has_key,
        reasoning_effort=(tunables.get("llm_chat_reasoning_effort") or None) if role == "chat" else None,
    )


class LLM:
    """A service's way to the LLM: one handle per role, resolved on every call."""

    def __init__(self, settings: Settings):

        self._keys = SecretStore.llm_keys(settings)
        self._clients: dict[tuple, LLMClient] = {}

        self.chat     = Role(self, "chat")
        self.instruct = Role(self, "instruct")
        self.embed    = Role(self, "embed")


    def client(self, endpoint: Endpoint) -> LLMClient:
        timeout, retries, no_proxy = (tunables.get(k) for k in ("llm_timeout_sec", "llm_max_retries", "llm_no_proxy"))
        key = (endpoint.provider, endpoint.version, timeout, retries, no_proxy)

        if key not in self._clients:
            self._clients[key] = LLMClient(
                base_url=endpoint.base_url,
                api_key=(self._keys.get(endpoint.provider) or "") if endpoint.has_key else "",
                timeout=timeout,
                no_proxy=no_proxy,
                max_retries=retries,
            )

        return self._clients[key]


    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()


class Role:
    def __init__(self, llm: LLM, name: str):

        self.llm  = llm
        self.name = name


    def endpoint(self) -> Endpoint | None:
        return resolve(self.name)


    async def complete(self, messages: list[dict[str, Any]], **kwargs) -> dict[str, Any]:
        """LLMClient.chat with this role's model (and reasoning effort, unless given)."""
        client, endpoint = self._bound()
        kwargs.setdefault("reasoning_effort", endpoint.reasoning_effort)

        return await client.chat(messages, model=endpoint.model, **kwargs)


    async def structured(self, messages: list[dict[str, Any]], **kwargs) -> Any:
        """LLMClient.structured with this role's model."""
        client, endpoint = self._bound()
        kwargs.setdefault("reasoning_effort", endpoint.reasoning_effort)

        return await client.structured(messages, model=endpoint.model, **kwargs)


    async def embed(self, texts: list[str]) -> list[list[float]]:
        client, endpoint = self._bound()

        return await client.embed(texts, model=endpoint.model)


    def _bound(self) -> tuple[LLMClient, Endpoint]:
        endpoint = self.endpoint()
        if endpoint is None:
            raise LLMNotConfigured(self.name)

        return self.llm.client(endpoint), endpoint
