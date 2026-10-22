"""A chat no task came from is temporary: idle long enough, it goes; a task's chat stays."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from agent_loop import history
from agent_loop.api import chat as chat_api
from common.errors import install_error_handlers
from common.storage.db import session
from common.storage.models import ChatMessage, ChatSession, Task, TaskVersion, TraceEvent
from common.utils import utcnow


def _chat(text: str, hours_ago: int = 0) -> str:
    session_id = history.ensure_session(None, text)
    history.add_message(session_id, "user", text)
    history.add_trace(session_id, None, "fs.list", {}, {"files": []}, "ok")

    with session() as db:
        db.get(ChatSession, session_id).updated_at = utcnow() - timedelta(hours=hours_ago)
        db.commit()

    return session_id


def _task_from(session_id: str, *, edited: bool = False) -> None:
    with session() as db:
        db.add(Task(id=f"task_{session_id}", title="Сводка", status="active", source_session_id=None if edited else session_id))
        if edited:
            db.add(TaskVersion(id=f"ver_{session_id}", task_id=f"task_{session_id}", version_no=1, spec={}, status="approved", session_id=session_id))
        db.commit()


def _left() -> set[str]:
    with session() as db:
        return {
            *(s for (s,) in db.query(ChatSession.id)),
            *(s for (s,) in db.query(ChatMessage.session_id)),
            *(s for (s,) in db.query(TraceEvent.session_id)),
        }


def test_only_idle_chats_without_a_task_go(settings):
    idle = _chat("Посмотри папку", hours_ago=30)
    fresh = _chat("Посмотри ещё раз", hours_ago=1)
    built = _chat("Собери задачу", hours_ago=30)
    edited = _chat("Поправь задачу", hours_ago=30)
    _task_from(built)
    _task_from(edited, edited=True)

    assert history.sweep_idle(24) == [idle]
    assert _left() == {fresh, built, edited}


def test_the_list_says_which_chats_stay(settings):
    temporary = _chat("Привет")
    built = _chat("Собери задачу")
    _task_from(built)

    chats = {chat["id"]: chat["task_id"] for chat in history.session_list()}

    assert chats == {temporary: None, built: f"task_{built}"}


@pytest.fixture
async def client(settings):
    class Loop:
        forgotten: list[str] = []

        def forget(self, ids):
            self.forgotten += ids

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.state.loop = Loop()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://agent") as client:
        yield client, app.state.loop


async def test_a_temporary_chat_can_go_now_and_a_tasks_chat_cannot(client):
    client, loop = client
    temporary = _chat("Привет")
    built = _chat("Собери задачу")
    _task_from(built)

    assert (await client.delete(f"/sessions/{temporary}")).status_code == 200
    assert (await client.delete(f"/sessions/{built}")).status_code == 409
    assert (await client.delete("/sessions/ses_missing")).status_code == 404
    assert loop.forgotten == [temporary]
    assert _left() == {built}
