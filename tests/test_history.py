"""The chat history the model sees on every step of a turn."""

from __future__ import annotations

from agent_loop import history


def _long_turn(session_id: str, tool_messages: int) -> None:
    history.add_message(session_id, "user", "Сделай отчёт по всем файлам")

    for i in range(tool_messages):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "docs__read", "arguments": "{}"}}
        history.add_message(session_id, "assistant", "", tool_calls=[call])
        history.add_message(session_id, "tool", "текст файла", tool_call_id=f"c{i}", name="docs__read")


def test_a_long_turn_keeps_the_users_request(settings):
    session_id = history.ensure_session(None, "Сделай отчёт")
    _long_turn(session_id, tool_messages=30)

    messages = history.llm_history(session_id, limit=40)

    assert messages[0] == {"role": "user", "content": "Сделай отчёт по всем файлам"}
    assert len(messages) == 61


def test_earlier_turns_are_still_cut_to_the_limit(settings):
    session_id = history.ensure_session(None, "Привет")
    for i in range(30):
        history.add_message(session_id, "user", f"вопрос {i}")
        history.add_message(session_id, "assistant", f"ответ {i}")

    messages = history.llm_history(session_id, limit=10)

    assert len(messages) == 10
    assert messages[0] == {"role": "user", "content": "вопрос 25"}
