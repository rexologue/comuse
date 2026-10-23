"""LLM providers on main's side: registering the models of LLM_* variables, the role
checks and one-off clients for them. main is the only writer of `llm_providers`, of the
role choice in `settings` and of the LLM keys store (common/llm.py reads them)."""

from __future__ import annotations

import logging
from typing import Any

from common import llm, tunables
from common.clients.llm_client import LLMClient
from common.errors import ApiError
from common.secrets import SecretStore
from common.settings import Settings
from common.storage.db import session
from common.storage.models import LLMProvider, Setting
from common.utils import new_id


log = logging.getLogger("providers")

# A check must answer while the user waits, whatever the timeout setting says.
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


# The `settings` row listing the LLM_* models already imported, as "base_url model".
ENV_IMPORTED_KEY = "llm_env_imported"


def register_env_models(settings: Settings) -> None:
    """LLM_* models become providers named `default…` once: an address and model
    imported before is left alone, so a provider the user changed or deleted stays as
    they left it. A provider with the same address and model is reused (renamed or
    not); a role still unset gets the model. From then on they are ordinary providers."""
    ls = settings.llm
    if ls is None:
        return

    with session() as db:
        row = db.get(Setting, ENV_IMPORTED_KEY)
        imported = list(row.value) if row is not None else []

    wanted = [("chat", "default", ls.base_url, ls.model_chat, ls.api_key)]

    # The same model for instruct needs nothing: instruct falls back to chat.
    if ls.model_instruct != ls.model_chat:
        wanted.append(("instruct", "default-instruct", ls.base_url, ls.model_instruct, ls.api_key))

    if ls.model_embed:
        wanted.append(("embed", "default-embed", ls.embed_base_url, ls.model_embed, ls.embed_api_key))

    for role, name, base_url, model, api_key in wanted:
        signature = f"{base_url} {model}"
        if signature in imported:
            continue

        provider_id = _find_or_add(settings, name, base_url, model, api_key)
        imported.append(signature)

        with session() as db:
            if db.get(Setting, llm.role_key(role)) is None:
                set_role(db, role, provider_id)
                log.info("role %s → provider %s from LLM_* variables", role, provider_id)

            row = db.get(Setting, ENV_IMPORTED_KEY) or Setting(key=ENV_IMPORTED_KEY)
            row.value = list(imported)
            db.add(row)
            db.commit()


def _find_or_add(settings: Settings, name: str, base_url: str, model: str, api_key: str) -> str:
    with session() as db:
        found = db.query(LLMProvider).filter(LLMProvider.base_url == base_url, LLMProvider.model == model).first()
        if found is not None:
            return found.id

    provider_id = new_id("prv_")
    has_key = False

    if api_key:
        try:
            SecretStore.llm_keys(settings).set(provider_id, api_key)
            has_key = True

        except ApiError as e:
            log.error("cannot store the key of %s: %s; the provider is added without it", name, e.message)

    with session() as db:
        db.add(LLMProvider(id=provider_id, name=free_name(db, name), base_url=base_url, model=model, has_key=has_key))
        db.commit()

    log.info("provider %s added from LLM_* variables: model %s", provider_id, model)

    return provider_id


def free_name(db, name: str) -> str:
    candidate, n = name, 1
    while db.query(LLMProvider).filter(LLMProvider.name == candidate).first() is not None:
        n += 1
        candidate = f"{name} {n}"

    return candidate


def set_role(db, role: str, provider_id: str | None) -> None:
    row = db.get(Setting, llm.role_key(role))

    if provider_id is None:
        if row is not None:
            db.delete(row)

    elif row is None:
        db.add(Setting(key=llm.role_key(role), value=provider_id))

    else:
        row.value = provider_id


def roles_of(db) -> dict[str, str | None]:
    """The provider set for each role (no fallback: an unset instruct is None here)."""
    rows = {row.key: row.value for row in db.query(Setting).filter(Setting.key.in_([llm.role_key(r) for r in llm.ROLES]))}
    return {role: rows.get(llm.role_key(role)) for role in llm.ROLES}


# -----------------------------
# CHECKS
# -----------------------------

def check_client(base_url: str, api_key: str) -> LLMClient:
    """A one-off client: no retries, a short timeout, not the services' cached ones."""
    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        timeout=min(tunables.get("llm_timeout_sec"), _CHECK_TIMEOUT_SEC),
        no_proxy=tunables.get("llm_no_proxy"),
        max_retries=0,
    )


def provider_key(settings: Settings, provider: LLMProvider) -> str:
    return (SecretStore.llm_keys(settings).get(provider.id) or "") if provider.has_key else ""


async def check(client: LLMClient, model: str, role: str) -> dict[str, Any]:
    """Whether the model can serve the role: chat calls a tool, instruct answers, embed
    returns a vector."""
    try:
        if role == "chat":
            reply = await client.chat(
                [{"role": "user", "content": "Проверка связи: вызови функцию ping."}],
                model=model,
                tools=[_PING],
                sampling=llm.role_sampling("chat"),
            )

            if not reply.get("tool_calls"):
                return {"ok": False, "error": "модель ответила текстом, а не вызовом функции: агенту нужен вызов инструментов"}

        elif role == "instruct":
            await client.chat([{"role": "user", "content": "Ответь одним словом: готов?"}], model=model, sampling=llm.role_sampling("instruct"))

        else:
            [vector] = await client.embed(["проверка связи"], model=model)
            return {"ok": True, "dimensions": len(vector)}

    except ApiError as e:
        return {"ok": False, "error": e.message}

    return {"ok": True}
