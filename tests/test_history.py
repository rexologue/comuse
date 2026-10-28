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


def test_a_task_notice_never_splits_a_tool_exchange(settings):
    """A run may post into the chat while a turn is waiting for its tool results."""
    session_id = history.ensure_session(None, "Найди отчёт")
    history.add_message(session_id, "user", "Найди отчёт")

    call = {"id": "c1", "type": "function", "function": {"name": "fs__list", "arguments": "{}"}}
    history.add_message(session_id, "assistant", "", tool_calls=[call])
    history.add_message(session_id, "notice", "Задача «Сводка»: готово")
    history.add_message(session_id, "tool", "файлы", tool_call_id="c1", name="fs__list")

    roles = [m["role"] for m in history.llm_history(session_id, limit=40)]

    assert roles == ["user", "assistant", "tool", "assistant"]
    assert history.llm_history(session_id, limit=40)[-1]["content"].endswith("Задача «Сводка»: готово")


def test_a_notice_is_not_the_users_turn(settings):
    session_id = history.ensure_session(None, "Привет")
    history.add_message(session_id, "user", "Сделай отчёт")
    for i in range(25):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "docs__read", "arguments": "{}"}}
        history.add_message(session_id, "assistant", "", tool_calls=[call])
        history.add_message(session_id, "tool", "текст", tool_call_id=f"c{i}", name="docs__read")
    history.add_message(session_id, "notice", "Задача «Сводка»: готово")

    assert history.llm_history(session_id, limit=10)[0] == {"role": "user", "content": "Сделай отчёт"}


def test_a_plain_client_finds_its_chat_by_the_conversation(settings):
    """A client without session_id sends the whole conversation back every time."""
    from agent_loop.api.chat import _earlier_dialogue

    session_id = history.ensure_session(None, "Сделай отчёт")
    history.add_message(session_id, "user", "Сделай отчёт")
    call = {"id": "c1", "type": "function", "function": {"name": "fs__list", "arguments": "{}"}}
    history.add_message(session_id, "assistant", "", tool_calls=[call])
    history.add_message(session_id, "tool", "файлы", tool_call_id="c1", name="fs__list")
    history.add_message(session_id, "assistant", "Готово: Отчёт.docx")
    history.add_message(session_id, "notice", "Задача «Сводка»: готово")

    # Another chat that started the same way but went elsewhere.
    other = history.ensure_session(None, "Сделай отчёт")
    history.add_message(other, "user", "Сделай отчёт")
    history.add_message(other, "assistant", "Какой именно?")

    sent = [
        {"role": "system", "content": "You are a helpful assistant"},
        {"role": "user", "content": "Сделай отчёт"},
        {"role": "assistant", "content": [{"type": "text", "text": "Готово: Отчёт.docx"}]},
        {"role": "user", "content": "Теперь каждую неделю"},
    ]

    assert history.find_session(_earlier_dialogue(sent)) == session_id
    assert history.find_session(_earlier_dialogue(sent[:2])) is None  # a first message starts a new chat


def test_a_call_left_without_a_result_does_not_break_the_chat(settings):
    """A turn that broke off mid-call: the next turns still make a history the API takes."""
    session_id = history.ensure_session(None, "Разложи файлы")
    history.add_message(session_id, "user", "Разложи файлы")
    calls = [{"id": f"c{i}", "type": "function", "function": {"name": "fs__move", "arguments": "{}"}} for i in (1, 2)]
    history.add_message(session_id, "assistant", "", tool_calls=calls)
    history.add_message(session_id, "tool", "ok", tool_call_id="c1", name="fs__move")
    history.add_message(session_id, "user", "Ну что там?")

    messages = history.llm_history(session_id, limit=20)

    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool", "user"]
    assert messages[3]["tool_call_id"] == "c2" and "прерван" in messages[3]["content"]


def test_the_transcript_is_the_chats_own_account_of_itself(settings):
    """What the compiler gets from agent-loop: text, tools that worked, texts to copy."""
    session_id = history.ensure_session(None, "Разложи файлы")
    history.add_message(session_id, "user", "Переложи счета в папку «Счета 2026»")
    call = {"id": "c1", "type": "function", "function": {"name": "fs__move", "arguments": "{}"}}
    message_id = history.add_message(session_id, "assistant", "", tool_calls=[call])
    history.add_trace(session_id, message_id, "fs.move", {"src": "a.pdf", "dst": "Счета 2026/"}, {"status": "ok", "output": {"path": "Счета 2026/a.pdf"}}, "ok")
    history.add_trace(session_id, message_id, "meta:task_list", {}, {"status": "ok", "output": []}, "ok")
    history.add_trace(session_id, message_id, "mail.send", {"to": ["x@company.local"]}, {"status": "error", "error": "нет"}, "error")

    chat = history.transcript(session_id)

    assert chat["text"].splitlines()[0] == "Пользователь: Переложи счета в папку «Счета 2026»"
    assert "→ fs.move(" in chat["text"] and "meta:" not in chat["text"]
    assert chat["done_tools"] == ["fs.move"]
    assert "Счета 2026/" in chat["given"] and "x@company.local" not in chat["given"]


def test_a_call_the_user_never_confirmed_reads_as_not_done(settings):
    session_id = history.ensure_session(None, "Скопируй")
    history.add_message(session_id, "user", "Скопируй b.txt в архив")
    message_id = history.add_message(session_id, "assistant", "Скопировать?")
    history.add_trace(session_id, message_id, "fs.copy", {"src": "b.txt"}, {"status": "needs_approval", "output": None}, "needs_approval")

    chat = history.transcript(session_id)

    assert "[НЕ выполнено: пользователь не подтвердил]" in chat["text"] and chat["done_tools"] == []
