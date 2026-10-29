"""Plugins (executor/plugins): approved code, draft, check, approval. A person approves a
draft here (through main) after reading its code, packages and tools; the chat only
writes drafts."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Account
from common.tools import ToolError
from executor.plugins import store
from executor.plugins.launch import check_draft as run_check


router = APIRouter()


class Draft(BaseModel):
    # plugin.json: title, description, tools (functions) or command (a server), env…
    manifest: dict[str, Any]
    # Relative path → text.
    files: dict[str, str] = Field(default_factory=dict)
    # Lines of requirements.txt, e.g. "pandas>=2"; resolved into requirements.lock.
    requirements: list[str] = Field(default_factory=list)


class Approval(BaseModel):
    # The hash of the draft the person read (its view's `hash`).
    hash: str


@router.get("/plugins", summary="Plugins: approved code and draft of each, and whether its server runs")
async def list_plugins(request: Request):
    plugins = request.app.state.plugins

    return {"plugins": [{**item, **plugins.state(item["name"])} for item in await asyncio.to_thread(store.plugin_list)]}


@router.get("/plugins/{name}/{which}", summary="The approved code or the draft: manifest, packages, tools, files")
async def get_code(name: str, which: Literal["current", "draft"], request: Request):
    return await _call(store.view, request.app.state.settings.storage, name, which, files=True)


@router.put("/plugins/{name}/draft", summary="Write the plugin's draft (new or in place)")
async def put_draft(name: str, body: Draft, request: Request):
    view = await _call(
        store.save_draft,
        request.app.state.settings.storage,
        name,
        manifest=body.manifest,
        files=body.files,
        requirements=body.requirements,
        taken_groups=request.app.state.registry.builtin_groups(),
    )
    # A new plugin's own account kind: accounts of it can be made to try the draft.
    request.app.state.plugins.offer_kind(name)

    return view


@router.delete("/plugins/{name}/draft", summary="Throw the draft away")
async def delete_draft(name: str, request: Request):
    await _call(store.drop_draft, request.app.state.settings.storage, name)
    request.app.state.plugins.offer_kind(name)

    return {"status": "deleted"}


@router.post("/plugins/{name}/draft/check", summary="Run the draft's server once to see its tools (asked by the person)")
async def check_draft(name: str, request: Request):
    try:
        return await run_check(request.app.state.settings.storage, name, request.app.state.plugins.first_account)

    except ToolError as e:
        raise ApiError(status_code=400, message=str(e)) from e


@router.post("/plugins/{name}/draft/approve", summary="Approve the draft: freeze it, install its packages, start its server")
async def approve(name: str, body: Approval, request: Request):
    before = _own_kinds(name, ("current",))
    view = await _call(store.approve, request.app.state.settings.storage, name, body.hash)
    await request.app.state.plugins.start(name)

    # A kind the new code no longer has (renamed, dropped): its accounts go as on removal.
    gone = _drop_accounts(before - _own_kinds(name, ("current",)), request.app.state.secrets)

    return {**view, **request.app.state.plugins.state(name), "accounts_deleted": gone}


@router.post("/plugins/{name}/restart", summary="Start the plugin's server again")
async def restart(name: str, request: Request):
    await request.app.state.plugins.start(name)

    return request.app.state.plugins.state(name)


@router.delete("/plugins/{name}", summary="Remove the plugin: its server, approved code, draft and the accounts of its own kind")
async def remove(name: str, request: Request):
    kinds = _own_kinds(name)

    await request.app.state.plugins.stop(name)
    await _call(store.remove, request.app.state.settings.storage, name)

    return {"status": "deleted", "accounts_deleted": _drop_accounts(kinds, request.app.state.secrets)}


def _drop_accounts(kinds: set[str], secrets) -> list[str]:
    """Accounts of a kind only this plugin had: their secrets are for nothing now, and a
    plugin made later with the same kind must not inherit them."""
    with session() as db:
        accounts = db.query(Account).filter(Account.kind.in_(kinds)).all() if kinds else []
        names = sorted(account.name for account in accounts)
        for account in accounts:
            secrets.delete(account.secret_ref)
            db.delete(account)
        db.commit()

    return names


def _own_kinds(name: str, rows: tuple[str, ...] = ("current", "draft")) -> set[str]:
    """The account kinds the plugin declared as its own (with fields), in its code or draft."""
    kinds = set()
    for which in rows:
        row = store.row(name, which)
        account = (row.manifest or {}).get("account") if row else None
        if account and account.get("fields") is not None:
            kinds.add(account["kind"])

    return kinds


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)

    except store.NotFound as e:
        raise ApiError(status_code=404, message=str(e)) from e

    except ToolError as e:
        raise ApiError(status_code=400, message=str(e)) from e
