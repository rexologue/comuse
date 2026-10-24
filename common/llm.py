"""Which model serves each role:

- chat — the agent in the chat and the compiler (multi-step work, calls tools);
- instruct — llm.* tools (single structured calls); the chat model when no other is
  set;
- embed — ranking tools for a request; none = the agent sees every tool.

A provider is one model at an OpenAI-compatible server (`llm_providers`); a role points
at any of them by id from the `settings` table. main writes both through /llm, and
registers the models of LLM_* variables as providers on start (main/providers.py).
Timeouts, retries, proxy and each role's sampling (temperature, top_p, thinking,
reasoning effort) are settings (common/tunables.py).

A role is resolved on every call, so a change applies without a restart. Clients are
kept per provider version and transport settings: a changed provider gets a new client,
and its key is read from the store only then."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from common import tunables
from common.clients.llm_client import LLMClient, Sampling
from common.errors import ApiError
from common.secrets import SecretStore
from common.settings import Settings
from common.storage.db import session
from common.storage.models import LLMProvider, Setting


ROLES = ("chat", "instruct", "embed")

# What each role does, as the settings page shows it (GET /llm).
ROLE_INFO = {
    "chat": ("Чат", "агент в чате и сборка задач; модель должна вызывать инструменты"),
    "instruct": ("Инструкции", "шаги, которые просят модель (llm.*); не назначена — работает модель чата"),
    "embed": ("Эмбеддинги", "отбор команд под запрос; не назначена — агент видит все команды"),
}


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
    sampling: Sampling = Sampling()


def role_sampling(role: str) -> Sampling:
    """The role's sampling settings (llm_<role>_temperature, …); embed has none."""
    if role not in tunables.SAMPLED_ROLES:
        return Sampling()

    thinking = tunables.get(f"llm_{role}_thinking")

    return Sampling(
        temperature=tunables.get(f"llm_{role}_temperature"),
        top_p=tunables.get(f"llm_{role}_top_p"),
        thinking={"on": True, "off": False}.get(thinking),
        reasoning_effort=tunables.get(f"llm_{role}_reasoning_effort") or None,
    )


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
        sampling=role_sampling(role),
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
            # The provider's earlier settings (another address, timeout, proxy): their
            # client and its connections go.
            for old in [k for k in self._clients if k[0] == endpoint.provider]:
                _close_later(self._clients.pop(old))

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
        """LLMClient.chat with this role's model and sampling (unless given)."""
        client, endpoint = self._bound()
        kwargs.setdefault("sampling", endpoint.sampling)

        return await client.chat(messages, model=endpoint.model, **kwargs)


    async def structured(self, messages: list[dict[str, Any]], **kwargs) -> Any:
        """LLMClient.structured with this role's model and sampling (unless given)."""
        client, endpoint = self._bound()
        kwargs.setdefault("sampling", endpoint.sampling)

        return await client.structured(messages, model=endpoint.model, **kwargs)


    async def embed(self, texts: list[str]) -> list[list[float]]:
        client, endpoint = self._bound()

        return await client.embed(texts, model=endpoint.model)


    def _bound(self) -> tuple[LLMClient, Endpoint]:
        endpoint = self.endpoint()
        if endpoint is None:
            raise LLMNotConfigured(self.name)

        return self.llm.client(endpoint), endpoint


def _close_later(client: LLMClient) -> None:
    try:
        asyncio.get_running_loop().create_task(client.close())

    except RuntimeError:
        pass  # No loop running (a script): the process ends with it anyway.
