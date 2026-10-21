"""LLM providers and which of them serve the roles (common/llm.py). main is the only
writer of `llm_providers`, of the provider choice in `settings` and of the LLM keys
store; the services resolve a role on every call."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from common import llm
from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.secrets import SecretStore
from common.settings import Settings
from common.storage.db import session
from common.storage.models import LLMProvider, Setting
from common.utils import new_id, utcnow
from main.auth import require_client


router = APIRouter(dependencies=[Depends(require_client)])

Effort = Literal["low", "medium", "high"]

# A check must answer while the user waits, whatever the provider's own timeout.
_CHECK_TIMEOUT_SEC = 60

# The smallest tool a chat model must be able to call: without tool calling the agent
# cannot work.
_PING = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Проверка связи. Вызови её, когда об этом просят.",
        "parameters": {"type": "object", "properties": {}},
    },
}


class ProviderFields(BaseModel):
    name: str | None = Field(None, min_length=1)
    base_url: str | None = None
    # A new key; "" or null removes the stored one. Never returned by the API.
    api_key: str | None = None
    no_proxy: bool | None = None
    timeout_sec: int | None = Field(None, ge=1)
    max_retries: int | None = Field(None, ge=0)
    # Empty: a provider for embeddings only; it cannot be the current one.
    model_chat: str | None = None
    model_instruct: str | None = None
    model_embed: str | None = None
    reasoning_effort: Effort | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("адрес начинается с http:// или https://, например http://localhost:11434/v1")

        return value

    @field_validator("model_chat", "model_instruct", "model_embed")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value is not None else None


class ProviderCreate(ProviderFields):
    name: str = Field(min_length=1)
    base_url: str


class Choice(BaseModel):
    """PATCH /llm: a field left out stays as it is; null clears the choice."""
    provider: str | None = None
    embed_provider: str | None = None


# -----------------------------
# OVERVIEW AND CHOICE
# -----------------------------

@router.get("/llm", summary="Providers, the current one and what serves each role")
async def overview(request: Request):
    return _overview(request.app.state.settings)


@router.patch("/llm", summary="Choose the current provider and the one for embeddings")
async def choose(body: Choice, request: Request):
    settings = request.app.state.settings
    _not_locked(settings)

    with session() as db:
        for field, key in (("provider", llm.CURRENT_KEY), ("embed_provider", llm.EMBED_KEY)):
            if field not in body.model_fields_set:
                continue

            provider_id = getattr(body, field)
            if provider_id is not None:
                provider = _provider(db, provider_id)

                if field == "provider" and not provider.model_chat:
                    raise ApiError(status_code=400, message=f"У провайдера «{provider.name}» нет модели chat: текущим он быть не может")

            _set_choice(db, key, provider_id)

        db.commit()

    return _overview(settings)


# -----------------------------
# PROVIDERS
# -----------------------------

@router.post("/llm/providers", summary="Add a provider; the first with a chat model becomes current")
async def create_provider(body: ProviderCreate, request: Request):
    settings = request.app.state.settings
    provider_id = new_id("prv_")

    with session() as db:
        _unique_name(db, body.name)

        db.add(LLMProvider(
            id=provider_id,
            name=body.name,
            base_url=body.base_url,
            has_key=bool(body.api_key),
            no_proxy=True if body.no_proxy is None else body.no_proxy,
            timeout_sec=body.timeout_sec or 300,
            max_retries=2 if body.max_retries is None else body.max_retries,
            model_chat=body.model_chat,
            model_instruct=body.model_instruct,
            model_embed=body.model_embed,
            reasoning_effort=body.reasoning_effort,
        ))

        if body.model_chat and _choice(db, llm.CURRENT_KEY) is None:
            _set_choice(db, llm.CURRENT_KEY, provider_id)

        db.commit()

    if body.api_key:
        SecretStore.llm_keys(settings).set(provider_id, body.api_key)

    return _provider_view(settings, provider_id)


@router.patch("/llm/providers/{provider_id}", summary="Change a provider; fields left out stay")
async def update_provider(provider_id: str, body: ProviderFields, request: Request):
    settings = request.app.state.settings
    changed = body.model_fields_set
    keys = SecretStore.llm_keys(settings)

    with session() as db:
        provider = _provider(db, provider_id)

        if body.name is not None and body.name != provider.name:
            _unique_name(db, body.name)

        for field in changed - {"api_key"}:
            value = getattr(body, field)

            # Required fields cannot be cleared; optional ones (models, effort) can.
            if value is None and field in ("name", "base_url", "no_proxy", "timeout_sec", "max_retries"):
                raise ApiError(status_code=400, message=f"Поле `{field}` нельзя очистить")

            if field == "model_chat" and value is None and _choice(db, llm.CURRENT_KEY) == provider_id:
                raise ApiError(status_code=400, message="У текущего провайдера должна быть модель chat")

            setattr(provider, field, value)

        if "api_key" in changed:
            provider.has_key = bool(body.api_key)

        # A new version even when only the key changed: services build a new client.
        provider.updated_at = utcnow()
        db.commit()

    if "api_key" in changed:
        if body.api_key:
            keys.set(provider_id, body.api_key)
        else:
            keys.delete(provider_id)

    return _provider_view(settings, provider_id)


@router.delete("/llm/providers/{provider_id}", summary="Remove a provider (not the current one)")
async def delete_provider(provider_id: str, request: Request):
    settings = request.app.state.settings

    with session() as db:
        provider = _provider(db, provider_id)

        if _choice(db, llm.CURRENT_KEY) == provider_id:
            raise ApiError(status_code=409, message="Это текущий провайдер: сначала сделайте текущим другой")

        if _choice(db, llm.EMBED_KEY) == provider_id:
            _set_choice(db, llm.EMBED_KEY, None)

        db.delete(provider)
        db.commit()

    SecretStore.llm_keys(settings).delete(provider_id)

    return {"deleted": provider_id}


@router.get("/llm/providers/{provider_id}/models", summary="Models the provider lists at /v1/models")
async def provider_models(provider_id: str, request: Request):
    client, _ = _client(request.app.state.settings, provider_id)

    try:
        return {"models": sorted(await client.models())}

    finally:
        await client.close()


@router.post("/llm/providers/{provider_id}/check", summary="Try every role's model: answers, calls tools, embeds")
async def check_provider(provider_id: str, request: Request):
    settings = request.app.state.settings
    client, models = _client(settings, provider_id)
    embed_client = _env_embed_client(settings) if provider_id == llm.ENV_PROVIDER else None

    try:
        return await _check(client, models, embed_client)

    finally:
        await client.close()
        if embed_client is not None:
            await embed_client.close()


# -----------------------------
# VIEWS
# -----------------------------

def _overview(settings: Settings) -> dict[str, Any]:
    with session() as db:
        providers = db.query(LLMProvider).order_by(LLMProvider.created_at).all()
        current, embed = _choice(db, llm.CURRENT_KEY), _choice(db, llm.EMBED_KEY)

    roles = {}
    for role in llm.ROLES:
        endpoint = llm.resolve(role, settings)
        roles[role] = {"provider": endpoint.provider, "model": endpoint.model} if endpoint else None

    items = [_env_dict(settings.llm)] if settings.llm is not None else []
    items += [_provider_dict(p, current=p.id == current, embed=p.id == embed) for p in providers]

    return {
        # LLM_MODEL in the environment decides every role; the choice cannot change.
        "locked": settings.llm is not None,
        "provider": llm.ENV_PROVIDER if settings.llm is not None else current,
        "embed_provider": None if settings.llm is not None else embed,
        "roles": roles,
        "providers": items,
    }


def _provider_view(settings: Settings, provider_id: str) -> dict[str, Any]:
    return next(item for item in _overview(settings)["providers"] if item["id"] == provider_id)


def _provider_dict(provider: LLMProvider, *, current: bool, embed: bool) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "has_key": provider.has_key,
        "no_proxy": provider.no_proxy,
        "timeout_sec": provider.timeout_sec,
        "max_retries": provider.max_retries,
        "model_chat": provider.model_chat,
        "model_instruct": provider.model_instruct,
        "model_embed": provider.model_embed,
        "reasoning_effort": provider.reasoning_effort,
        "current": current,
        "embeddings": embed,
        "locked": False,
        "created_at": provider.created_at.isoformat(),
        "updated_at": provider.updated_at.isoformat(),
    }


def _env_dict(ls) -> dict[str, Any]:
    return {
        "id": llm.ENV_PROVIDER,
        "name": "Переменные окружения LLM_*",
        "base_url": ls.base_url,
        "has_key": bool(ls.api_key),
        "no_proxy": ls.no_proxy,
        "timeout_sec": ls.timeout,
        "max_retries": ls.max_retries,
        "model_chat": ls.model_chat,
        "model_instruct": ls.model_instruct,
        "model_embed": ls.model_embed or None,
        "reasoning_effort": ls.chat_reasoning_effort or None,
        "embed_base_url": ls.embed_base_url if ls.embed_base_url != ls.base_url else None,
        "current": True,
        "embeddings": False,
        "locked": True,
    }


# -----------------------------
# HELPERS
# -----------------------------

def _not_locked(settings: Settings) -> None:
    if settings.llm is not None:
        raise ApiError(status_code=409, message="LLM задана переменными окружения (LLM_MODEL): выбор через API не меняется")


def _provider(db, provider_id: str) -> LLMProvider:
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise ApiError(status_code=404, message=f"Нет провайдера `{provider_id}`")

    return provider


def _unique_name(db, name: str) -> None:
    if db.query(LLMProvider).filter(LLMProvider.name == name).first() is not None:
        raise ApiError(status_code=409, message=f"Провайдер «{name}» уже есть")


def _choice(db, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row is not None else None


def _set_choice(db, key: str, provider_id: str | None) -> None:
    row = db.get(Setting, key)

    if provider_id is None:
        if row is not None:
            db.delete(row)

    elif row is None:
        db.add(Setting(key=key, value=provider_id))

    else:
        row.value = provider_id


def _client(settings: Settings, provider_id: str) -> tuple[LLMClient, dict[str, str | None]]:
    """A one-off client for a check (not the services' cached ones) and the provider's
    model per role."""
    if provider_id == llm.ENV_PROVIDER:
        if settings.llm is None:
            raise ApiError(status_code=404, message="LLM в переменных окружения не задана")

        ls = settings.llm
        client = LLMClient(base_url=ls.base_url, api_key=ls.api_key, timeout=min(ls.timeout, _CHECK_TIMEOUT_SEC), no_proxy=ls.no_proxy, max_retries=0)
        return client, {"chat": ls.model_chat, "instruct": ls.model_instruct, "embed": ls.model_embed or None}

    with session() as db:
        provider = _provider(db, provider_id)

    api_key = (SecretStore.llm_keys(settings).get(provider_id) or "") if provider.has_key else ""
    client = LLMClient(
        base_url=provider.base_url,
        api_key=api_key,
        timeout=min(provider.timeout_sec, _CHECK_TIMEOUT_SEC),
        no_proxy=provider.no_proxy,
        max_retries=0,
    )

    return client, {role: llm.role_model(provider, role) for role in llm.ROLES}


def _env_embed_client(settings: Settings) -> LLMClient | None:
    """LLM_EMBED_BASE_URL: the environment's embeddings live on a server of their own."""
    ls = settings.llm
    if ls.embed_base_url == ls.base_url:
        return None

    return LLMClient(base_url=ls.embed_base_url, api_key=ls.embed_api_key, timeout=min(ls.timeout, _CHECK_TIMEOUT_SEC), no_proxy=ls.no_proxy, max_retries=0)


async def _check(client: LLMClient, models: dict[str, str | None], embed_client: LLMClient | None = None) -> dict[str, Any]:
    """Each role's model in turn; one failure does not stop the others."""
    try:
        listed: dict[str, Any] = {"ok": True, "models": sorted(await client.models())}

    except ApiError as e:
        listed = {"ok": False, "error": e.message}

    roles: dict[str, Any] = {}

    async def attempt(role: str, call) -> None:
        model = models[role]
        if not model:
            roles[role] = None
            return

        try:
            roles[role] = {"model": model, "ok": True, **(await call(model) or {})}

        except ApiError as e:
            roles[role] = {"model": model, "ok": False, "error": e.message}

    async def chat(model: str) -> dict[str, Any]:
        reply = await client.chat([{"role": "user", "content": "Проверка связи: вызови функцию ping."}], model=model, tools=[_PING])
        if not reply.get("tool_calls"):
            return {"ok": False, "error": "модель ответила текстом, а не вызовом функции: агенту нужен вызов инструментов"}

        return {}

    async def instruct(model: str) -> None:
        await client.chat([{"role": "user", "content": "Ответь одним словом: готов?"}], model=model)

    async def embed(model: str) -> dict[str, Any]:
        [vector] = await (embed_client or client).embed(["проверка связи"], model=model)
        return {"dimensions": len(vector)}

    await attempt("chat", chat)
    await attempt("instruct", instruct)
    await attempt("embed", embed)

    return {
        "ok": all(result["ok"] for result in roles.values() if result is not None),
        "list": listed,
        "roles": roles,
    }
