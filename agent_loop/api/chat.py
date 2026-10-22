"""OpenAI-compatible chat endpoint. The session id travels in `metadata.session_id`
(or the `session_id` field, e.g. through an SDK's extra_body); the reply carries it back
so a client can continue. A plain client that knows nothing of it sends the whole
conversation back every time instead, and the chat is found by that conversation."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_loop import history
from agent_loop.loop import TurnResult
from common.errors import ApiError
from common.utils import new_id


router = APIRouter()

MODEL_NAME = "office-agent"

# While a turn runs, SSE clients get a comment every few seconds to keep the stream alive.
_KEEPALIVE_SEC = 10


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


@router.get("/v1/models", summary="The single model this service exposes")
async def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "fintech2026"}]}


@router.post("/v1/chat/completions", summary="Chat turn (OpenAI-compatible)")
async def chat_completions(body: ChatRequest, request: Request):
    session_id = body.session_id or (body.metadata or {}).get("session_id")
    user_text = _last_user_text(body.messages)

    if session_id is None:
        session_id = history.find_session(_earlier_dialogue(body.messages))

    loop = request.app.state.loop

    if not body.stream:
        result = await loop.run_turn(session_id, user_text)
        return _completion(result)

    task = asyncio.create_task(loop.run_turn(session_id, user_text))

    return StreamingResponse(_stream(task), media_type="text/event-stream")


@router.get("/sessions", summary="Chat sessions")
async def sessions():
    return {"sessions": history.session_list()}


@router.get("/sessions/{session_id}", summary="Messages and tool trace of a session")
async def session_detail(session_id: str):
    return history.session_view(session_id)


@router.delete("/sessions/{session_id}", summary="Delete a temporary chat now (a task's chat stays)")
async def delete_session(session_id: str, request: Request):
    if not history.session_exists(session_id):
        raise ApiError(status_code=404, message=f"Session `{session_id}` not found")

    if task_id := history.task_chats().get(session_id):
        raise ApiError(status_code=409, message=f"Это чат задачи `{task_id}`: он живёт, пока жива задача")

    history.delete_session(session_id)
    request.app.state.loop.forget([session_id])

    return {"deleted": session_id}


class PostedMessage(BaseModel):
    content: str = Field(min_length=1)


@router.post("/sessions/{session_id}/messages", summary="A task tells its user something in the chat it was built in")
async def post_message(session_id: str, body: PostedMessage):
    if not history.session_exists(session_id):
        raise ApiError(status_code=404, message=f"Session `{session_id}` not found")

    # Its own role: it may arrive in the middle of a turn, and it is not the model's words.
    message_id = history.add_message(session_id, "notice", body.content)

    return {"session_id": session_id, "message_id": message_id}


async def _stream(task: asyncio.Task):
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_KEEPALIVE_SEC)

        except TimeoutError:
            yield ": keepalive\n\n"

    try:
        result: TurnResult = task.result()

    except ApiError as e:
        yield f"data: {json.dumps(e.body(), ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    chunk_id = new_id("chatcmpl-")
    created = int(time.time())

    for delta, finish in (({"role": "assistant", "content": result.content}, None), ({}, "stop")):
        chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": MODEL_NAME,
            "session_id": result.session_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    yield "data: [DONE]\n\n"


def _completion(result: TurnResult) -> dict[str, Any]:
    return {
        "id": new_id("chatcmpl-"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_NAME,
        "session_id": result.session_id,
        "tool_events": result.tool_events,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.content},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _text(message)

    raise ApiError(status_code=400, message="No user message in `messages`")


def _earlier_dialogue(messages: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """What the client says was said before its last message: user messages and the
    assistant's answers with text (system prompts and tool messages are its own)."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=0)

    return [
        (message["role"], _text(message).strip())
        for message in messages[:last_user]
        if message.get("role") in ("user", "assistant") and _text(message).strip()
    ]


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))

    return str(content or "")
