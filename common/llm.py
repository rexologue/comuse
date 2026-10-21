"""Which provider and model serve each role:

- chat — the agent in the chat and the compiler (multi-step work);
- instruct — llm.* tools and template markup (single structured calls); the chat model
  when the provider names none;
- embed — ranking tools for a request; none = the agent sees every tool.

With LLM_MODEL in the environment, the environment decides every role
(common/settings.py) and the API cannot change it. Otherwise one provider from
`llm_providers` is current and serves chat and instruct; embeddings come from it too
unless another provider is chosen for them. Both choices are rows of the `settings`
table, which main writes through /llm.

A role is resolved on every call, so switching the provider applies without a restart.
Clients are kept per provider version: a changed provider gets a new client, and its
key is read from the store only then."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.secrets import SecretStore
from common.settings import LLMSettings, Settings
from common.storage.db import session
from common.storage.models import LLMProvider, Setting


ROLES = ("chat", "instruct", "embed")

# Rows of the `settings` table: the current provider and the one for embeddings.
CURRENT_KEY = "llm_provider"
EMBED_KEY = "llm_embed_provider"

# The provider made of LLM_* variables.
ENV_PROVIDER = "env"


class LLMNotConfigured(ApiError):
    def __init__(self, role: str):
        super().__init__(
            status_code=503,
            message=(
                f"Модель для роли {role} не подключена: добавьте провайдера (POST /llm/providers) "
                f"и сделайте его текущим (PATCH /llm)"
            ),
        )


@dataclass(slots=True, frozen=True)
class Endpoint:
    provider: str
    # Changes with every change of the provider: the client cache key.
    version: str
    base_url: str
    no_proxy: bool
    timeout: int
    max_retries: int
    model: str
    reasoning_effort: str | None = None
    # The env provider carries its key; a stored provider's key is in the store.
    api_key: str | None = None
    has_key: bool = False


def resolve(role: str, settings: Settings) -> Endpoint | None:
    """The endpoint serving `role` now; None when nothing is configured for it."""
    if settings.llm is not None:
        return _from_env(role, settings.llm)

    with session() as db:
        chosen = {row.key: row.value for row in db.query(Setting).filter(Setting.key.in_((CURRENT_KEY, EMBED_KEY)))}
        provider_id = (chosen.get(EMBED_KEY) if role == "embed" else None) or chosen.get(CURRENT_KEY)
        provider = db.get(LLMProvider, provider_id) if provider_id else None

    if provider is None or not (model := role_model(provider, role)):
        return None

    return Endpoint(
        provider=provider.id,
        version=provider.updated_at.isoformat(),
        base_url=provider.base_url,
        no_proxy=provider.no_proxy,
        timeout=provider.timeout_sec,
        max_retries=provider.max_retries,
        model=model,
        reasoning_effort=provider.reasoning_effort if role == "chat" else None,
        has_key=provider.has_key,
    )


def role_model(provider: LLMProvider, role: str) -> str | None:
    return {
        "chat": provider.model_chat,
        "instruct": provider.model_instruct or provider.model_chat,
        "embed": provider.model_embed,
    }[role]


def _from_env(role: str, ls: LLMSettings) -> Endpoint | None:
    common = {"provider": ENV_PROVIDER, "version": ENV_PROVIDER, "no_proxy": ls.no_proxy, "timeout": ls.timeout, "max_retries": ls.max_retries}

    if role == "embed":
        if not ls.model_embed:
            return None

        return Endpoint(**common, base_url=ls.embed_base_url, model=ls.model_embed, api_key=ls.embed_api_key)

    if role == "chat":
        return Endpoint(**common, base_url=ls.base_url, model=ls.model_chat, reasoning_effort=ls.chat_reasoning_effort or None, api_key=ls.api_key)

    return Endpoint(**common, base_url=ls.base_url, model=ls.model_instruct, api_key=ls.api_key)


class LLM:
    """A service's way to the LLM: one handle per role, resolved on every call."""

    def __init__(self, settings: Settings):

        self.settings = settings

        self._keys = SecretStore.llm_keys(settings)
        self._clients: dict[tuple[str, str, str], LLMClient] = {}

        self.chat     = Role(self, "chat")
        self.instruct = Role(self, "instruct")
        self.embed    = Role(self, "embed")


    def client(self, endpoint: Endpoint) -> LLMClient:
        key = (endpoint.provider, endpoint.version, endpoint.base_url)

        if key not in self._clients:
            api_key = endpoint.api_key
            if api_key is None:
                api_key = (self._keys.get(endpoint.provider) or "") if endpoint.has_key else ""

            self._clients[key] = LLMClient(
                base_url=endpoint.base_url,
                api_key=api_key,
                timeout=endpoint.timeout,
                no_proxy=endpoint.no_proxy,
                max_retries=endpoint.max_retries,
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
        return resolve(self.name, self.llm.settings)


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
