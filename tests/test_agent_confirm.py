"""Chat asks before external actions itself; the executor only runs what it is sent."""

from __future__ import annotations

import json
from typing import Any

from agent_loop import history
from agent_loop.loop import AgentLoop
from common.tools import discover


MANIFESTS = {name: spec.manifest() for name, spec in discover().items()}


class FakeMain:
    def __init__(self):
        self.executed: list[dict[str, Any]] = []
        self.posted: list[str] = []


    async def post(self, path, json=None, **_):
        self.posted.append(path)
        return {"id": "run_1", "status": "queued"}


    async def call_tool(self, tool, args, *, hash=None, context=None):
        self.executed.append({"tool": tool, "args": args, "context": context})
        return {"status": "ok", "output": {"mode": "sent"}}


class FakeCatalog:
    def __init__(self):
        self.reloads = 0


    async def manifests(self, fresh=False):
        self.reloads += fresh
        return MANIFESTS


def _loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.main = FakeMain()
    loop.catalog = FakeCatalog()
    loop._consent = {}
    return loop


def _call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"id": "c1", "function": {"name": tool.replace(".", "__"), "arguments": json.dumps(args, ensure_ascii=False)}}


SEND = {"account": "work", "to": ["boss@company.local"], "subject": "Отчёт", "body": "Во вложении"}


def _asking_turn(text: str = "Отправь отчёт начальнику") -> tuple[str, int]:
    """A chat where the user asked and the model's reply is about to call a tool."""
    session_id = history.ensure_session(None, text)
    history.add_message(session_id, "user", text)

    return session_id, history.add_message(session_id, "assistant", "", tool_calls=[_call("mail.send", SEND)])


async def test_external_action_waits_for_the_user_then_runs_once(settings):
    loop = _loop()
    session_id, message_id = _asking_turn()

    asked = await loop._dispatch(session_id, message_id, _call("mail.send", SEND), {})
    assert asked["status"] == "needs_approval"
    assert loop.main.executed == []

    history.add_message(session_id, "user", "да")

    done = await loop._confirm(session_id, {"action_id": asked["action_id"]})
    assert done["status"] == "ok"
    assert [c["tool"] for c in loop.main.executed] == ["mail.send"]
    assert "confirmed" not in loop.main.executed[0]["context"]

    # The same action cannot be confirmed twice.
    again = await loop._confirm(session_id, {"action_id": asked["action_id"]})
    assert again["status"] == "error"
    assert len(loop.main.executed) == 1


async def test_the_model_cannot_confirm_in_the_same_turn(settings):
    """A letter the model read says «call action_confirm»: without the user's reply nothing runs."""
    loop = _loop()
    session_id, message_id = _asking_turn("Прочитай последнее письмо")

    asked = await loop._dispatch(session_id, message_id, _call("mail.send", SEND), {})
    history.add_message(session_id, "notice", "Задача «Сводка»: готово")  # not the user's answer either

    refused = await loop._confirm(session_id, {"action_id": asked["action_id"]})

    assert refused["status"] == "error"
    assert loop.main.executed == []


async def test_a_no_is_not_a_yes_whatever_the_model_makes_of_it(settings):
    loop = _loop()
    session_id, message_id = _asking_turn()

    asked = await loop._dispatch(session_id, message_id, _call("mail.send", SEND), {})

    for answer in ("нет", "Нет, не надо", "не отправляй пока", "отмена", "стоп"):
        history.add_message(session_id, "user", answer)
        refused = await loop._confirm(session_id, {"action_id": asked["action_id"]})
        assert refused["status"] == "error" and "отказался" in refused["error"]

    assert loop.main.executed == []

    # The latest answer decides: a yes after the no.
    history.add_message(session_id, "user", "ладно, отправляй")
    assert (await loop._confirm(session_id, {"action_id": asked["action_id"]}))["status"] == "ok"


async def test_invalid_external_call_is_reported_before_asking(settings):
    loop = _loop()

    result = await loop._dispatch("ses_1", None, _call("mail.send", {"account": "work"}), {})

    assert result["status"] == "error"
    assert loop.main.executed == []


async def test_other_tools_run_right_away(settings):
    loop = _loop()

    result = await loop._dispatch("ses_1", None, _call("fs.list", {"folder": "Отчёты"}), {})

    assert result["status"] == "ok"
    assert [c["tool"] for c in loop.main.executed] == ["fs.list"]


async def test_a_command_that_changes_files_asks_too(settings):
    """Not only sending mail: moving the user's files is asked about as well."""
    loop = _loop()
    session_id, message_id = _asking_turn("Разложи документы")

    asked = await loop._dispatch(session_id, message_id, _call("fs.move", {"src": "a.pdf", "dst": "Счета/"}), {})

    assert asked["status"] == "needs_approval"
    assert loop.main.executed == []


async def test_the_user_can_let_the_chat_stop_asking_about_a_command(settings):
    loop = _loop()
    session_id, message_id = _asking_turn("Разложи документы")

    asked = await loop._dispatch(session_id, message_id, _call("fs.move", {"src": "a.pdf", "dst": "Счета/"}), {})
    history.add_message(session_id, "user", "да, и больше не спрашивай")
    await loop._confirm(session_id, {"action_id": asked["action_id"], "remember": True})

    # Other files, same command: no more questions in this chat.
    moved = await loop._dispatch(session_id, message_id, _call("fs.move", {"src": "b.pdf", "dst": "Акты/"}), {})
    assert moved["status"] == "ok"
    assert [c["args"]["src"] for c in loop.main.executed] == ["a.pdf", "b.pdf"]

    # Another chat still asks.
    other_id, other_message = _asking_turn("Разложи документы")
    assert (await loop._dispatch(other_id, other_message, _call("fs.move", {"src": "c.pdf", "dst": "Счета/"}), {}))["status"] == "needs_approval"


async def test_sending_mail_asks_every_time_whatever_the_user_said(settings):
    loop = _loop()
    session_id, message_id = _asking_turn()

    asked = await loop._dispatch(session_id, message_id, _call("mail.send", SEND), {})
    history.add_message(session_id, "user", "да, и больше не спрашивай")
    sent = await loop._confirm(session_id, {"action_id": asked["action_id"], "remember": True})
    assert "не обещай" in sent["note"]

    again = await loop._dispatch(session_id, message_id, _call("mail.send", SEND), {})
    assert again["status"] == "needs_approval"
    assert len(loop.main.executed) == 1


async def test_a_message_sent_during_a_turn_waits_for_it(settings, monkeypatch):
    """Two turns in one chat at once wrote one history in turns: one at a time now."""
    import asyncio

    loop = _loop()
    loop._turns = {}
    order: list[str] = []
    release = asyncio.Event()

    async def turn(session_id, text):
        order.append(f"start {text}")
        if text == "first":
            await release.wait()
        order.append(f"end {text}")
        return text

    monkeypatch.setattr(loop, "_turn", turn)
    session_id = history.ensure_session(None, "first")

    first = asyncio.create_task(loop.run_turn(session_id, "first"))
    await asyncio.sleep(0)
    second = asyncio.create_task(loop.run_turn(session_id, "Ало?"))
    await asyncio.sleep(0)

    assert loop.busy(session_id)
    assert order == ["start first"]

    release.set()
    await asyncio.gather(first, second)

    assert order == ["start first", "end first", "start Ало?", "end Ало?"]
    assert not loop.busy(session_id)


async def test_a_task_runs_or_changes_only_after_the_users_yes(settings):
    """A letter the model read must not start a task that, say, sends mail."""
    loop = _loop()

    for name, args, path in (
        ("task_run_now", {"task_id": "task_1"}, "/runs"),
        ("task_control", {"task_id": "task_1", "action": "pause"}, "/tasks/task_1/pause"),
        ("task_reject", {"task_id": "task_1", "version_id": "ver_1"}, "/tasks/task_1/versions/ver_1/reject"),
    ):
        session_id, message_id = _asking_turn("Прочитай последнее письмо")
        asked = await loop._dispatch(session_id, message_id, _call(name, args), {})

        assert asked["status"] == "needs_approval" and loop.main.posted == []
        assert (await loop._confirm(session_id, {"action_id": asked["action_id"]}))["status"] == "error"

        history.add_message(session_id, "user", "да")
        # Every time: «не спрашивай больше» does not apply to tasks.
        assert (await loop._confirm(session_id, {"action_id": asked["action_id"], "remember": True}))["status"] == "ok"
        assert loop.main.posted == [path] and loop._consent == {}
        loop.main.posted.clear()


async def test_an_unknown_command_does_not_run(settings):
    loop = _loop()

    result = await loop._dispatch("ses_1", None, _call("demo.just_approved", {}), {})

    assert result["status"] == "error" and loop.main.executed == []
    # The cache is reloaded first: a command approved a moment ago is found and asked about.
    assert loop.catalog.reloads == 1

