"""Mail accounts. Only the non-secret part reaches SQLite; the password goes to the
keyring and never leaves the executor."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Account
from common.utils import new_id
from internal_tools.mail.adapters import imap_smtp
from internal_tools.mail.adapters.presets import PRESETS


router = APIRouter()


class AccountCreate(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_\-]{1,40}$")
    kind: Literal["imap_smtp"] = "imap_smtp"
    email: str
    # gmail | yandex | mailru | outlook — fills hosts and ports.
    preset: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str = Field(min_length=1)


@router.get("/accounts", summary="Configured accounts (no secrets)")
async def list_accounts():
    with session() as db:
        rows = db.query(Account).order_by(Account.name).all()

    return {"accounts": [_public(row) for row in rows]}


@router.get("/accounts/presets", summary="Known mail providers")
async def list_presets():
    return {"presets": PRESETS}


@router.post("/accounts", summary="Create or replace an account")
async def create_account(body: AccountCreate, request: Request):
    config: dict[str, Any] = {}

    if body.preset:
        if body.preset not in PRESETS:
            raise ApiError(status_code=400, message=f"Unknown preset `{body.preset}`", details={"presets": list(PRESETS)})
        config.update(PRESETS[body.preset])

    config.update(body.config)
    config["email"] = body.email
    config.setdefault("username", body.email)

    missing = [key for key in ("imap_host", "smtp_host") if not config.get(key)]
    if missing:
        raise ApiError(status_code=400, message=f"Missing mail settings: {missing}; pass a preset or config")

    secret_ref = f"account:{body.name}"
    request.app.state.secrets.set(secret_ref, body.secret)

    with session() as db:
        row = db.query(Account).filter(Account.name == body.name).one_or_none()
        row = row or Account(id=new_id("acc_"), name=body.name)
        row.kind = body.kind
        row.config = config
        row.secret_ref = secret_ref
        db.add(row)
        db.commit()

    return _public(row)


@router.post("/accounts/{name}/check", summary="Try to log in to IMAP and SMTP")
async def check_account(name: str, request: Request):
    account = request.app.state.runtime._get_account(name)

    try:
        await asyncio.to_thread(imap_smtp.check, account.config, account.secret)

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
        db.delete(row)
        db.commit()

    return {"status": "deleted"}


def _public(row: Account) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "kind": row.kind, "config": row.config}
