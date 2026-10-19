"""Agent meta tools: turning tasks on and approving drafts stay with the user."""

from __future__ import annotations

from agent_loop import meta_tools


class FakeMain:
    def __init__(self, approvals):
        self.approvals = approvals
        self.posted: list[str] = []


    async def get(self, path, params=None):
        return {"approvals": self.approvals}


    async def post(self, path, json=None):
        self.posted.append(path)
        return {"status": "answered"}


PENDING = [
    {"id": "apr_task", "kind": "version_approve", "payload": {}},
    {"id": "apr_ask", "kind": "ask_user", "payload": {"question": "За какой месяц?"}},
]


def test_agent_has_no_task_approve():
    assert "task_approve" not in meta_tools.NAMES


async def test_agent_sees_only_questions():
    result = await meta_tools.call(FakeMain(PENDING), "ses", "approvals_list", {})

    assert [a["approval_id"] for a in result["output"]] == ["apr_ask"]


async def test_agent_cannot_approve_a_task():
    main = FakeMain(PENDING)

    result = await meta_tools.call(main, "ses", "approval_answer", {"approval_id": "apr_task", "approve": True})
    assert result["status"] == "error"

    assert main.posted == []


async def test_agent_relays_answers_to_questions():
    main = FakeMain(PENDING)

    result = await meta_tools.call(main, "ses", "approval_answer", {"approval_id": "apr_ask", "approve": True, "answer": "сентябрь"})

    assert result["status"] == "ok"
    assert main.posted == ["/approvals/apr_ask/answer"]
