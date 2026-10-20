"""Chat asks before external actions itself; the executor only runs what it is sent."""

from __future__ import annotations

from typing import Any

from agent_loop import history
from agent_loop.loop import AgentLoop
from common.tools import discover


MANIFESTS = {name: spec.manifest() for name, spec in discover().items()}


class FakeMain:
    def __init__(self):
        self.executed: list[dict[str, Any]] = []


    async def post(self, path, json=None, **_):
        assert path == "/executor/execute"
        self.executed.append(json)
        return {"status": "ok", "output": {"mode": "sent"}}


class FakeCatalog:
    async def manifests(self):
        return MANIFESTS


def _loop() -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.main = FakeMain()
    loop.catalog = FakeCatalog()
    return loop


def _call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    import json

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
