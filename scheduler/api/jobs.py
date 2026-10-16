from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel


router = APIRouter()


class ReloadBody(BaseModel):
    task_id: str | None = None


@router.post("/reload", summary="Re-read active tasks and rebuild their triggers")
async def reload(body: ReloadBody, request: Request):
    return request.app.state.scheduler.reload(body.task_id)


@router.get("/jobs", summary="What runs when")
async def jobs(request: Request):
    return {"jobs": request.app.state.scheduler.jobs()}
