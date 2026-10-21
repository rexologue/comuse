"""LLM providers and the roles they serve (common/llm.py, main/providers.py)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator

from common.errors import ApiError
from common.secrets import SecretStore
from common.storage.db import session
from common.storage.models import LLMProvider
from common.utils import new_id, utcnow
from main import providers
from main.auth import require_client


router = APIRouter(dependencies=[Depends(require_client)])

RoleName = Literal["chat", "instruct", "embed"]


def _url(value: str | None) -> str | None:
    if value is None:
        return None

    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("адрес начинается с http:// или https://, например http://localhost:11434/v1")

    return value


class ProviderFields(BaseModel):
    name: str | None = Field(None, min_length=1)
    base_url: str | None = None
    model: str | None = Field(None, min_length=1)
    # A new key; "" or null removes the stored one. Never returned by the API.
    api_key: str | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return _url(value)


class ProviderCreate(ProviderFields):
    name: str = Field(min_length=1)
    base_url: str
    model: str = Field(min_length=1)


class Roles(BaseModel):
    """PATCH /llm: a role left out stays; null clears it (instruct then follows chat)."""
    chat: str | None = None
    instruct: str | None = None
    embed: str | None = None


class ModelsQuery(BaseModel):
    """A server to list the models of: an address (and key), or a stored provider's."""
    base_url: str | None = None
    api_key: str | None = None
    provider: str | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return _url(value)


class CheckQuery(BaseModel):
    role: RoleName


# -----------------------------
# ROLES
# -----------------------------

@router.get("/llm", summary="Providers and the one serving each role")
async def overview():
    return _overview()


@router.patch("/llm", summary="Set the providers of roles; each is checked for its role first")
async def set_roles(body: Roles, request: Request):
    settings = request.app.state.settings
    changes = {role: getattr(body, role) for role in body.model_fields_set}

    # All or nothing: every provider is checked for its role before anything changes.
    problems = {}
    for role, provider_id in changes.items():
        if provider_id is None:
            continue

        with session() as db:
            provider = _provider(db, provider_id)

        result = await _check(settings, provider, role)
        if not result["ok"]:
            problems[role] = f"{provider.name}: {result['error']}"

    if problems:
        raise ApiError(status_code=400, message="Роли не изменены: проверка не пройдена", details=problems)

    with session() as db:
        for role, provider_id in changes.items():
            providers.set_role(db, role, provider_id)

        db.commit()

    return _overview()


# -----------------------------
# PROVIDERS
# -----------------------------

@router.post("/llm/providers", summary="Add a provider: a model at a server")
async def create_provider(body: ProviderCreate, request: Request):
    provider_id = new_id("prv_")

    with session() as db:
        _unique_name(db, body.name)
        db.add(LLMProvider(id=provider_id, name=body.name, base_url=body.base_url, model=body.model, has_key=bool(body.api_key)))

        # The key first: a provider that says it has one must find it in the store.
        if body.api_key:
            SecretStore.llm_keys(request.app.state.settings).set(provider_id, body.api_key)

        db.commit()

    return _provider_view(provider_id)


@router.patch("/llm/providers/{provider_id}", summary="Change a provider; fields left out stay")
async def update_provider(provider_id: str, body: ProviderFields, request: Request):
    changed = body.model_fields_set

    with session() as db:
        provider = _provider(db, provider_id)

        for field in changed - {"api_key"}:
            value = getattr(body, field)
            if value is None:
                raise ApiError(status_code=400, message=f"Поле `{field}` нельзя очистить")

            if field == "name" and value != provider.name:
                _unique_name(db, value)

            setattr(provider, field, value)

        if "api_key" in changed:
            keys = SecretStore.llm_keys(request.app.state.settings)
            if body.api_key:
                keys.set(provider_id, body.api_key)
            else:
                keys.delete(provider_id)

            provider.has_key = bool(body.api_key)

        # A new version even when only the key changed: services build a new client.
        provider.updated_at = utcnow()
        db.commit()

    return _provider_view(provider_id)


@router.delete("/llm/providers/{provider_id}", summary="Remove a provider no role uses")
async def delete_provider(provider_id: str, request: Request):
    with session() as db:
        provider = _provider(db, provider_id)

        used = [role for role, assigned in providers.roles_of(db).items() if assigned == provider_id]
        if used:
            raise ApiError(status_code=409, message=f"Провайдер назначен на роли {used}: сначала назначьте на них другой")

        db.delete(provider)
        db.commit()

    SecretStore.llm_keys(request.app.state.settings).delete(provider_id)

    return {"deleted": provider_id}


@router.post("/llm/models", summary="Models a server lists at /v1/models")
async def list_models(body: ModelsQuery, request: Request):
    settings = request.app.state.settings

    if body.provider is not None:
        with session() as db:
            provider = _provider(db, body.provider)

        base_url, api_key = provider.base_url, providers.provider_key(settings, provider)

    elif body.base_url is not None:
        base_url, api_key = body.base_url, body.api_key or ""

    else:
        raise ApiError(status_code=400, message="Нужен `base_url` или `provider`")

    client = providers.check_client(base_url, api_key)
    try:
        return {"models": sorted(await client.models())}

    finally:
        await client.close()


@router.post("/llm/providers/{provider_id}/check", summary="Can the provider serve a role")
async def check_provider(provider_id: str, body: CheckQuery, request: Request):
    with session() as db:
        provider = _provider(db, provider_id)

    return {"role": body.role, "model": provider.model, **await _check(request.app.state.settings, provider, body.role)}


# -----------------------------
# VIEWS
# -----------------------------

def _overview() -> dict[str, Any]:
    with session() as db:
        items = db.query(LLMProvider).order_by(LLMProvider.created_at).all()
        assigned = providers.roles_of(db)

    by_id = {provider.id: provider for provider in items}

    # Instruct with no provider of its own follows chat.
    serving = {**assigned, "instruct": assigned["instruct"] or assigned["chat"]}

    def role_view(role: str, provider_id: str | None) -> dict[str, Any] | None:
        if provider_id not in by_id:
            return None

        provider = by_id[provider_id]
        view = {"provider": provider.id, "name": provider.name, "model": provider.model}

        if role == "instruct":
            view["follows_chat"] = assigned["instruct"] is None

        return view

    return {
        "roles": {role: role_view(role, provider_id) for role, provider_id in serving.items()},
        "providers": [
            _provider_dict(provider, [role for role, provider_id in serving.items() if provider_id == provider.id])
            for provider in items
        ],
    }


def _provider_view(provider_id: str) -> dict[str, Any]:
    return next(item for item in _overview()["providers"] if item["id"] == provider_id)


def _provider_dict(provider: LLMProvider, roles: list[str]) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "model": provider.model,
        "has_key": provider.has_key,
        "roles": roles,
        "created_at": provider.created_at.isoformat(),
        "updated_at": provider.updated_at.isoformat(),
    }


async def _check(settings, provider: LLMProvider, role: str) -> dict[str, Any]:
    client = providers.check_client(provider.base_url, providers.provider_key(settings, provider))

    try:
        return await providers.check(client, provider.model, role)

    finally:
        await client.close()


def _provider(db, provider_id: str) -> LLMProvider:
    provider = db.get(LLMProvider, provider_id)
    if provider is None:
        raise ApiError(status_code=404, message=f"Нет провайдера `{provider_id}`")

    return provider


def _unique_name(db, name: str) -> None:
    if db.query(LLMProvider).filter(LLMProvider.name == name).first() is not None:
        raise ApiError(status_code=409, message=f"Провайдер «{name}» уже есть")
