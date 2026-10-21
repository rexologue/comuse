"""Settings the user changes while the agent runs (common/tunables.py). main is the only
writer of the `settings` table; the services read it on every use."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from common import tunables
from common.errors import ApiError
from common.storage.db import session
from common.storage.models import Setting
from main.auth import require_client


router = APIRouter(dependencies=[Depends(require_client)])


@router.get("/settings", summary="Settings: value, source (env | db | default), whether it can change")
async def list_settings():
    return {"settings": tunables.describe()}


@router.patch("/settings", summary="Change settings; null returns a setting to its default")
async def update_settings(body: dict[str, Any]):
    # All or nothing: every value is checked before anything is written.
    errors = {key: _problem(key, value) for key, value in body.items()}
    errors = {key: problem for key, problem in errors.items() if problem}

    if errors:
        raise ApiError(status_code=400, message="Настройки не изменены", details=errors)

    with session() as db:
        for key, value in body.items():
            row = db.get(Setting, key)

            if value is None:
                if row is not None:
                    db.delete(row)

            elif row is None:
                db.add(Setting(key=key, value=value))

            else:
                row.value = value

        db.commit()

    return {"settings": tunables.describe()}


def _problem(key: str, value: Any) -> str | None:
    tunable = tunables.TUNABLES.get(key)

    if tunable is None:
        return "нет такой настройки"

    if tunables.locked(key):
        return f"задана переменной окружения {tunable.env}, через API не меняется"

    if value is None:
        return None

    try:
        tunable.parse(value)

    except ValueError as e:
        return str(e)

    # A stored path is the user's own folder: relative to what would it be?
    if tunable.kind == "dir" and not Path(value).expanduser().is_absolute():
        return "нужен полный путь к папке"

    return None
