"""External tools (common/external_tools): drafts, versions, approval. A person approves
a version here (through main) after reading its code and packages; the chat only
writes drafts."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from common.errors import ApiError
from common.external_tools import store
from common.tools import ToolError


router = APIRouter()


class Draft(BaseModel):
    # tool.json: title, description, card, confirm, timeout, input_schema, output_schema.
    manifest: dict[str, Any]
    # Relative path → text; main.py with `def run(args, ctx)` is required.
    files: dict[str, str]
    # Lines of requirements.txt, e.g. "pandas>=2"; resolved into requirements.lock.
    requirements: list[str] = Field(default_factory=list)


class Approval(BaseModel):
    # The hash of the version the person read (its view's `hash`).
    hash: str


@router.get("/external-tools", summary="External tools with their versions")
async def list_tools():
    return {"tools": await asyncio.to_thread(store.tool_list)}


@router.get("/external-tools/{name}/versions/{version}", summary="One version: manifest, packages, files")
async def get_version(name: str, version: int, request: Request):
    return await _call(store.version_view, request.app.state.settings.storage, name, version, files=True)


@router.put("/external-tools/{name}/draft", summary="Write the tool's draft (new or in place)")
async def put_draft(name: str, body: Draft, request: Request):
    return await _call(
        store.save_draft,
        request.app.state.settings.storage,
        name,
        manifest=body.manifest,
        files=body.files,
        requirements=body.requirements,
        builtin_groups=request.app.state.registry.builtin_groups(),
    )


@router.delete("/external-tools/{name}/draft", summary="Throw the draft away")
async def delete_draft(name: str, request: Request):
    await _call(store.drop_draft, request.app.state.settings.storage, name)

    return {"status": "deleted"}


@router.post("/external-tools/{name}/versions/{version}/approve", summary="Approve a draft: freeze it, install its packages, make it current")
async def approve(name: str, version: int, body: Approval, request: Request):
    view = await _call(store.approve, request.app.state.settings.storage, name, version, body.hash)
    request.app.state.registry.load()

    return view


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)

    except ToolError as e:
        raise ApiError(status_code=400, message=str(e)) from e
