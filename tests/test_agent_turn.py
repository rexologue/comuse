"""A chat turn as the stress test found it wanting: the tools of a «да» chosen by what it
answers, a call repeated after the same failure, a turn out of steps that says what it
did, results cut so that every field stays, a message too long refused before it is kept."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from agent_loop import history, meta_tools
from agent_loop.loop import AgentLoop
from common import tunables
from common.utils import shrink
from tests.test_agent_confirm import MANIFESTS


class FakeChat:
    """The model, scripted: each step returns the next reply; the last repeats."""

    def __init__(self, replies: list[dict[str, Any]]):
        self.replies = replies
        self.seen: list[list[dict[str, Any]]] = []
        self.tools_given: list[list[dict[str, Any]]] = []


    def endpoint(self):
        return object()


    async def complete(self, messages, tools=None):
        self.seen.append(messages)
        self.tools_given.append(tools or [])
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeCatalog:
    def __init__(self):
        self.queries: list[str] = []


    async def select(self, query, always=None):
        self.queries.append(query)
        return [MANIFESTS["fs.list"]]


    async def manifests(self, fresh=False):
        return MANIFESTS


class FakeMain:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []


    async def call_tool(self, tool, args, *, hash=None, context=None):
        self.calls.append((tool, args))
        return {"status": "error", "error": "Папка не найдена"}


    async def publish(self, *a, **k):
        pass


def _loop(replies) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.main = FakeMain()
    loop.catalog = FakeCatalog()
    loop._consent = {}
    loop._turns = {}
    loop.llm = type("LLM", (), {"chat": FakeChat(replies)})()
    return loop


def _calling(tool: str, args: dict) -> dict:
    return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": tool.replace(".", "__"), "arguments": json.dumps(args)}}]}


async def test_a_short_reply_chooses_tools_by_what_it_answers(settings):
    loop = _loop([{"content": "Готово", "tool_calls": []}])
    session_id = history.ensure_session(None, "Перешли счета")
    history.add_message(session_id, "user", "Перешли счета бухгалтеру и пометь их меткой invoices")
    history.add_message(session_id, "assistant", "Переслать 3 письма и пометить их меткой invoices?")

    await loop._turn(session_id, "да")

    assert loop.catalog.queries == ["Перешли счета бухгалтеру и пометь их меткой invoices\nПереслать 3 письма и пометить их меткой invoices?\nда"]


async def test_a_tool_reached_outside_the_selection_joins_the_turn(settings):
    loop = _loop([_calling("mail.mark", {"account": "w"}), {"content": "Готово", "tool_calls": []}])
    session_id = history.ensure_session(None, "x")

    await loop._turn(session_id, "Пометь письмо 7 прочитанным в ящике w")

    # The second step of the turn is offered mail.mark with its schema.
    assert "mail__mark" in {tool["function"]["name"] for tool in loop.llm.chat.tools_given[1]}


async def test_the_same_failing_call_is_not_made_again(settings, monkeypatch):
    monkeypatch.setattr(tunables, "get", lambda key: {"agent_max_iterations": 4, "agent_history_chars": 32000}.get(key, 24))
    listing = _calling("fs.list", {"folder": "Нет такой"})
    loop = _loop([listing, listing, listing, listing, {"content": "Не нашёл папку «Нет такой».", "tool_calls": []}])
    session_id = history.ensure_session(None, "x")

    result = await loop._turn(session_id, "Что в папке «Нет такой»?")

    assert loop.main.calls == [("fs.list", {"folder": "Нет такой"})]
    # Out of steps: the model is asked, without tools, what it did — not a bare «stopped».
    assert result.content == "Не нашёл папку «Нет такой»." and loop.llm.chat.tools_given[-1] == []


async def test_a_chat_too_long_for_the_model_is_sent_again_shorter(settings):
    """The server's context is not known beforehand: its «too long» gets a shorter history,
    not a chat broken for good."""
    from common.errors import ApiError

    class Overflowing(FakeChat):
        async def complete(self, messages, tools=None):
            if sum(len(m["content"]) for m in messages[1:]) > 12000:
                self.seen.append(messages)
                raise ApiError(400, "LLM responded with HTTP 400", {"body": {"error": {"message": "This model's maximum context length is 28768 tokens"}}})
            return await super().complete(messages, tools)

    loop = _loop([])
    loop.llm.chat = Overflowing([{"content": "Во втором — счёт", "tool_calls": []}])
    session_id = history.ensure_session(None, "x")
    for i in range(3):
        history.add_message(session_id, "user", f"письмо {i}: " + "т" * 9000)
        history.add_message(session_id, "assistant", "Прочитал")

    result = await loop._turn(session_id, "что было во втором письме?")

    assert result.content == "Во втором — счёт"
    assert len(loop.llm.chat.seen) == 2  # 32000 characters of history, then 16000


def test_a_cut_result_keeps_every_field():
    text = shrink({"status": "ok", "output": {"text": "x" * 30000, "truncated": True, "attachments": ["a.pdf"]}}, 6000)
    output = json.loads(text)["output"]

    assert len(text) <= 6000 and output["truncated"] is True and output["attachments"] == ["a.pdf"]
    assert re.search(r"… \[обрезано: показано \d+ из 30000 символов\]$", output["text"])

    listed = json.loads(shrink({"files": [{"path": f"f{i}.txt"} for i in range(2000)]}, 6000))["files"]
    assert listed[-1].endswith("из 2000]")


def test_a_result_cut_for_the_model_says_so_first():
    from agent_loop.loop import _shown

    short = {"status": "ok", "output": {"text": "акт"}}
    assert json.loads(_shown(short)) == short

    shown = json.loads(_shown({"status": "ok", "output": {"text": "т" * 20000, "truncated": False}}))
    assert list(shown) == ["note", "status", "output"] and "max_chars" in shown["note"]
    assert "обрезано" in shown["output"]["text"]


async def test_a_message_too_long_is_refused_before_it_is_kept(settings):
    import httpx
    from fastapi import FastAPI

    from agent_loop.api import chat as chat_api
    from common.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.state.loop = None

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://agent") as client:
        reply = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "а" * 20001}]})

    assert reply.status_code == 413 and "файл" in json.dumps(reply.json(), ensure_ascii=False)
    assert history.session_list() == []


async def test_the_chat_sees_accounts_runs_drafts_and_settings():
    class Main:
        async def get(self, path):
            return {
                "/accounts": {"accounts": [{"name": "Test", "kind": "imap_smtp", "config": {"email": "oki@umail.com"}}]},
                "/tasks": {"tasks": [{"id": "task_1", "title": "Итоги", "status": "active", "schedule": "по будням в 09:00", "cards": [{"text": "Для каждого файла", "children": [{"text": "Прочитать «‹f›»"}]}], "settings": {"mail": {"send_directly": True}}}]},
                "/approvals": {"approvals": [{"task_id": "task_1", "version_id": "ver_2", "summary": "черновик", "cards": []}]},
                "/tasks/task_1/runs?include_empty=true": {"runs": [{"id": "run_9", "status": "failed", "dry_run": False, "trigger": "cron", "started_at": "t", "finished_at": "t", "error": "нет папки"}]},
            }[path]

    accounts = await meta_tools.call(Main(), "ses", "accounts_list", {})
    tasks = await meta_tools.call(Main(), "ses", "task_list", {})
    runs = await meta_tools.call(Main(), "ses", "task_runs", {"task_id": "task_1"})

    assert accounts["output"] == [{"name": "Test", "kind": "imap_smtp", "address": "oki@umail.com"}]
    assert tasks["output"][0]["draft"]["version_id"] == "ver_2" and tasks["output"][0]["settings"] == {"mail": {"send_directly": True}}
    # Steps by their text: the list of every task must fit what the model sees.
    assert tasks["output"][0]["steps"] == ["Для каждого файла", "  Прочитать «‹f›»"]
    assert runs["output"][0]["run_id"] == "run_9" and runs["output"][0]["error"] == "нет папки"


@pytest.mark.parametrize(("offset", "length", "truncated"), [(0, 100, True), (200, 50, False)])
async def test_a_long_document_is_read_in_windows(settings, env, offset, length, truncated):
    from common.tools import ToolContext
    from executor.paths import WorkspacePaths
    from internal_tools.docs.read import Input, read

    (env / "workspace" / "long.txt").write_text("0123456789" * 25, encoding="utf-8")
    ctx = ToolContext(mode="chat", settings=settings, paths=WorkspacePaths(env / "workspace", settings.storage.data_dir))

    out = await read(Input(path="long.txt", max_chars=100, offset=offset), ctx)

    assert (len(out.text), out.truncated, out.documents[0].text_length) == (length, truncated, 250)
