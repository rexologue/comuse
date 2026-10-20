"""Chat sessions, messages and the tool-call trace; agent-loop is their only writer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import ChatMessage, ChatSession, TraceEvent
from common.utils import new_id


def ensure_session(session_id: str | None, first_text: str) -> str:
    with session() as db:
        if session_id:
            if db.get(ChatSession, session_id) is None:
                raise ApiError(status_code=404, message=f"Session `{session_id}` not found")

            return session_id

        chat = ChatSession(id=new_id("ses_"), title=first_text.strip().splitlines()[0][:80] if first_text.strip() else "Новый чат")
        db.add(chat)
        db.commit()

        return chat.id


def session_exists(session_id: str) -> bool:
    with session() as db:
        return db.get(ChatSession, session_id) is not None


def add_message(
    session_id: str,
    role: str,
    content: str = "",
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    tool_call_id: str | None = None,
    name: str | None = None,
) -> int:
    with session() as db:
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            name=name,
        )
        db.add(message)

        chat = db.get(ChatSession, session_id)
        if chat is not None:
            chat.status = "active"

        db.commit()

        return message.id


def llm_history(session_id: str, limit: int) -> list[dict[str, Any]]:
    """Last `limit` messages as OpenAI messages, never starting mid tool-exchange. The
    current turn always stays whole, from the user's message on, however many tool calls
    it has taken: cutting it would leave the model without the request it is working on."""
    with session() as db:
        messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)

        rows = messages.order_by(ChatMessage.id.desc()).limit(limit).all()
        rows.reverse()

        last_user = (
            db.query(func.max(ChatMessage.id))
            .filter(ChatMessage.session_id == session_id, ChatMessage.role == "user")
            .scalar()
        )

        if last_user is not None and (not rows or rows[0].id > last_user):
            rows = messages.filter(ChatMessage.id >= last_user).order_by(ChatMessage.id).all()

    # A window that starts with tool results (or the call that produced them) would
    # be rejected by the API; drop until the first user message.
    while rows and rows[0].role != "user":
        rows.pop(0)

    messages: list[dict[str, Any]] = []
    for row in rows:
        if row.role == "assistant":
            message: dict[str, Any] = {"role": "assistant", "content": row.content or ""}
            if row.tool_calls:
                message["tool_calls"] = row.tool_calls
            messages.append(message)

        elif row.role == "tool":
            messages.append({"role": "tool", "tool_call_id": row.tool_call_id, "content": row.content})

        else:
            messages.append({"role": row.role, "content": row.content})

    return messages


def add_trace(session_id: str, message_id: int | None, tool: str, args: dict[str, Any], result: Any, status: str) -> int:
    with session() as db:
        event = TraceEvent(
            session_id=session_id,
            message_id=message_id,
            tool=tool,
            args=args,
            result=result,
            status=status,
        )
        db.add(event)
        db.commit()

        return event.id


def get_trace(trace_id: int) -> TraceEvent | None:
    with session() as db:
        return db.get(TraceEvent, trace_id)


def update_trace(trace_id: int, *, status: str, result: Any) -> None:
    with session() as db:
        event = db.get(TraceEvent, trace_id)
        event.status = status
        event.result = result
        db.commit()


def used_tools(session_id: str) -> set[str]:
    with session() as db:
        rows = db.query(TraceEvent.tool).filter(TraceEvent.session_id == session_id).distinct().all()

    return {tool for (tool,) in rows}


def session_view(session_id: str) -> dict[str, Any]:
    with session() as db:
        chat = db.get(ChatSession, session_id)
        if chat is None:
            raise ApiError(status_code=404, message=f"Session `{session_id}` not found")

        messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
        trace = db.query(TraceEvent).filter(TraceEvent.session_id == session_id).order_by(TraceEvent.id).all()

    return {
        "id": chat.id,
        "title": chat.title,
        "created_at": chat.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_calls": m.tool_calls,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
        "trace": [
            {"id": t.id, "tool": t.tool, "args": t.args, "status": t.status, "result": t.result}
            for t in trace
        ],
    }


def session_list(limit: int = 50) -> list[dict[str, Any]]:
    with session() as db:
        rows = db.query(ChatSession).order_by(ChatSession.updated_at.desc()).limit(limit).all()

    return [{"id": r.id, "title": r.title, "updated_at": r.updated_at.isoformat()} for r in rows]
