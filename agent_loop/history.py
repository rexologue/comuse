"""Chat sessions, messages and the tool-call trace; agent-loop is their only writer.

A chat a task was built or edited in stays as long as the task does: its runs report
there. Any other chat is temporary: idle for `chat_idle_hours`, it goes."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func

from common.errors import ApiError
from common.storage.db import session
from common.storage.models import ChatMessage, ChatSession, Task, TaskVersion, TraceEvent
from common.utils import new_id, strings_in, truncate, utcnow

# The result of a call whose turn broke off before it answered.
_NO_RESULT = '{"status": "error", "error": "Вызов прерван: результата нет"}'


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


def find_session(dialogue: list[tuple[str, str]]) -> str | None:
    """The chat whose conversation ends with `dialogue`: (role, text) pairs of the user's
    messages and the assistant's answers, as a plain OpenAI client sends them back each
    time instead of a session id. Tool exchanges and task notices are not in a client's
    copy, so they are left out here too; the newest matching chat wins."""
    if not dialogue:
        return None

    role, text = dialogue[-1]

    with session() as db:
        rows = (
            db.query(ChatMessage.session_id)
            .filter(ChatMessage.role == role, ChatMessage.content == text)
            .order_by(ChatMessage.id.desc())
            .limit(50)
            .all()
        )

    for session_id in dict.fromkeys(session_id for (session_id,) in rows):
        if _dialogue_tail(session_id, len(dialogue)) == [(r, t.strip()) for r, t in dialogue]:
            return session_id

    return None


def _dialogue_tail(session_id: str, count: int) -> list[tuple[str, str]]:
    """The last `count` messages as the client saw them: text the model wrote next to a
    tool call ("Сейчас посмотрю папку") never reached it."""
    tail: list[tuple[str, str]] = []

    with session() as db:
        rows = (
            db.query(ChatMessage.role, ChatMessage.content, ChatMessage.tool_calls)
            .filter(ChatMessage.session_id == session_id, ChatMessage.role.in_(("user", "assistant")), ChatMessage.content != "")
            .order_by(ChatMessage.id.desc())
            .yield_per(50)
        )

        for role, content, tool_calls in rows:
            if tool_calls:
                continue

            tail.append((role, (content or "").strip()))
            if len(tail) == count:
                break

    return list(reversed(tail))


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

        # The chat list is newest first by the last message.
        chat = db.get(ChatSession, session_id)
        if chat is not None:
            chat.updated_at = utcnow()

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
    # Tool calls still waiting for their results, and task notices that arrived meanwhile:
    # an API rejects anything between a call and its results.
    unanswered: list[str] = []
    deferred: list[dict[str, Any]] = []

    def close_unanswered() -> None:
        # A turn that broke off mid-call left calls with no result; without one every
        # later turn would be refused by the API.
        messages.extend({"role": "tool", "tool_call_id": call_id, "content": _NO_RESULT} for call_id in unanswered)
        unanswered.clear()

    for row in rows:
        if row.role == "notice":
            notice = {"role": "assistant", "content": f"[Сообщение задачи, не мои слова] {row.content}"}
            (deferred if unanswered else messages).append(notice)
            continue

        if row.role == "tool":
            if row.tool_call_id in unanswered:
                messages.append({"role": "tool", "tool_call_id": row.tool_call_id, "content": row.content})
                unanswered.remove(row.tool_call_id)

        else:
            close_unanswered()

            if row.role == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": row.content or ""}
                if row.tool_calls:
                    message["tool_calls"] = row.tool_calls
                    unanswered = [call["id"] for call in row.tool_calls]
                messages.append(message)

            else:
                messages.append({"role": row.role, "content": row.content})

        if not unanswered and deferred:
            messages += deferred
            deferred = []

    close_unanswered()

    return messages + deferred


def user_wrote_after(session_id: str, message_id: int | None) -> bool:
    """Whether the user has written anything since that message (a task notice does not count)."""
    if message_id is None:
        return False

    with session() as db:
        reply = (
            db.query(ChatMessage.id)
            .filter(ChatMessage.session_id == session_id, ChatMessage.role == "user", ChatMessage.id > message_id)
            .first()
        )

    return reply is not None


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
            # message_id: the assistant message whose tool call this was.
            {"id": t.id, "message_id": t.message_id, "tool": t.tool, "args": t.args, "status": t.status, "result": t.result}
            for t in trace
        ],
    }


def session_list(limit: int = 50) -> list[dict[str, Any]]:
    """Latest chats; `task_id` is the task of a chat that stays, null for a temporary one."""
    with session() as db:
        rows = db.query(ChatSession).order_by(ChatSession.updated_at.desc()).limit(limit).all()

    tasks = task_chats()

    return [{"id": r.id, "title": r.title, "updated_at": r.updated_at.isoformat(), "task_id": tasks.get(r.id)} for r in rows]


# -----------------------------
# TEMPORARY CHATS
# -----------------------------

def task_chats() -> dict[str, str]:
    """Chat → task, for the chats a task was built or edited in (read from the
    compiler's tables)."""
    with session() as db:
        built = db.query(Task.source_session_id, Task.id).filter(Task.source_session_id.is_not(None)).all()
        edited = db.query(TaskVersion.session_id, TaskVersion.task_id).filter(TaskVersion.session_id.is_not(None)).all()

    return {session_id: task_id for session_id, task_id in [*edited, *built]}


def delete_session(session_id: str) -> None:
    with session() as db:
        db.query(TraceEvent).filter(TraceEvent.session_id == session_id).delete(synchronize_session=False)
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete(synchronize_session=False)
        db.query(ChatSession).filter(ChatSession.id == session_id).delete(synchronize_session=False)
        db.commit()


def sweep_idle(idle_hours: int) -> list[str]:
    """Delete temporary chats nobody has written in for `idle_hours`; returns their ids."""
    cutoff = utcnow() - timedelta(hours=idle_hours)

    with session() as db:
        idle = [chat_id for (chat_id,) in db.query(ChatSession.id).filter(ChatSession.updated_at < cutoff)]

    kept = task_chats()
    gone = [chat_id for chat_id in idle if chat_id not in kept]

    for chat_id in gone:
        delete_session(chat_id)

    return gone


def transcript(session_id: str) -> dict[str, Any]:
    """The chat for whoever turns it into a task (the compiler, through main): its text
    with every tool call and result, the tools that worked, and the texts the user gave
    (their messages and the arguments of the calls) to be copied word for word."""
    with session() as db:
        messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
        trace = (
            db.query(TraceEvent)
            .filter(TraceEvent.session_id == session_id, ~TraceEvent.tool.startswith("meta:"))
            .order_by(TraceEvent.id)
            .all()
        )

    calls_by_message: dict[int | None, list[TraceEvent]] = {}
    for event in trace:
        calls_by_message.setdefault(event.message_id, []).append(event)

    lines: list[str] = []

    for message in messages:
        if message.role == "user":
            lines.append(f"Пользователь: {message.content}")

        elif message.role == "assistant":
            if message.content:
                lines.append(f"Ассистент: {message.content}")

            for event in calls_by_message.get(message.id, []):
                result = event.result or {}
                payload = result.get("output") if result.get("status") in ("ok", "needs_approval") else result.get("error")
                lines.append(
                    f"→ {event.tool}({json.dumps(event.args, ensure_ascii=False)}) "
                    f"[{event.status}]: {truncate(payload, 800)}"
                )

    return {
        "text": "\n".join(lines),
        "done_tools": sorted({event.tool for event in trace if event.status in ("ok", "confirmed")}),
        "given": [m.content for m in messages if m.role == "user" and m.content]
        + [s for event in trace if event.status in ("ok", "needs_approval", "confirmed") for s in strings_in(event.args)],
    }
