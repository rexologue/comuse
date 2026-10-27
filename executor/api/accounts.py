"""Accounts: a name, a kind, non-secret settings and a password. Only the settings
reach SQLite; the password goes to the keyring and never leaves the executor. What a
kind needs (presets, required settings, a login check) is declared by the tool group
that owns it (common/tools/accounts.py)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Account
from common.tools import ToolError
from common.tools.accounts import AccountKind, account_kinds, get_account_kind
from common.utils import new_id


router = APIRouter()


class AccountCreate(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_\-]{1,40}$")
    kind: str
    # A known provider of the kind (GET /accounts/kinds); fills settings under `config`.
    preset: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    # Left out when editing an account: its current password stays.
    secret: str | None = Field(default=None, min_length=1)


@router.get("/accounts", summary="Configured accounts (no secrets)")
async def list_accounts():
    with session() as db:
        rows = db.query(Account).order_by(Account.name).all()

    return {"accounts": [_public(row) for row in rows]}


@router.get("/accounts/kinds", summary="Account kinds the tool groups offer, with their presets")
async def list_kinds():
    return {
        "kinds": {
            name: {"title": kind.title, "fields": [f.manifest() for f in kind.fields], "secret_title": kind.secret_title, "presets": kind.presets}
            for name, kind in account_kinds().items()
        }
    }


@router.post("/accounts", summary="Create or replace an account (no secret: keep the current one)")
async def create_account(body: AccountCreate, request: Request):
    kind = _kind(body.kind)

    if body.preset and body.preset not in kind.presets:
        raise ApiError(status_code=400, message=f"Unknown preset `{body.preset}`", details={"presets": list(kind.presets)})

    try:
        config = kind.prepare({**kind.presets.get(body.preset or "", {}), **body.config})

    except ValueError as e:
        raise ApiError(status_code=400, message=str(e)) from e

    secret_ref = f"account:{body.name}"
    secrets = request.app.state.secrets

    if body.secret is not None:
        secrets.set(secret_ref, body.secret)

    elif secrets.get(secret_ref) is None:
        raise ApiError(status_code=400, message=f"У аккаунта `{body.name}` ещё нет пароля: передайте secret")

    with session() as db:
        row = db.query(Account).filter(Account.name == body.name).one_or_none()
        row = row or Account(id=new_id("acc_"), name=body.name)
        row.kind = kind.name
        row.config = config
        row.secret_ref = secret_ref
        db.add(row)
        db.commit()

    # Plugins that work for accounts of this kind take it into their servers.
    await request.app.state.plugins.account_changed(kind.name)

    return _public(row)


@router.post("/accounts/{name}/check", summary="Try to log in with the account")
async def check_account(name: str, request: Request):
    try:
        account = request.app.state.runtime.load_credential(name)

    except ToolError as e:
        raise ApiError(status_code=404, message=str(e)) from e

    kind = _kind(account.kind)

    if kind.check is None:
        return {"status": "unchecked"}

    try:
        await asyncio.to_thread(kind.check, account.config, account.secret)

    except Exception as e:
        raise ApiError(status_code=502, message=f"Login failed for `{name}`", details={"error": str(e)}) from e

    return {"status": "ok"}


@router.delete("/accounts/{name}", summary="Delete an account and its secret")
async def delete_account(name: str, request: Request):
    with session() as db:
        row = db.query(Account).filter(Account.name == name).one_or_none()
        if row is None:
            raise ApiError(status_code=404, message=f"Account `{name}` not found")

        request.app.state.secrets.delete(row.secret_ref)
        kind = row.kind
        db.delete(row)
        db.commit()

    await request.app.state.plugins.account_changed(kind)

    return {"status": "deleted"}


def _kind(name: str) -> AccountKind:
    kind = get_account_kind(name)
    if kind is None:
        raise ApiError(status_code=400, message=f"Unknown account kind `{name}`", details={"kinds": list(account_kinds())})

    return kind


def _public(row: Account) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "kind": row.kind, "config": row.config}
