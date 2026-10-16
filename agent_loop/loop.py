"""One chat turn: history → tools → LLM → tool calls through main → … → answer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agent_loop import history, meta_tools
from agent_loop.catalog import ToolCatalog, to_function
from agent_loop.prompts import system_prompt
from common.clients.llm_client import LLMClient
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.settings import Settings
from common.tools import tool_name
from common.utils import truncate


log = logging.getLogger("loop")

# Tool results are cut before they go back into the context window.
_RESULT_CHARS = 6000


@dataclass(slots=True)
class TurnResult:
    session_id: str
    content: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    def __init__(self, settings: Settings, llm: LLMClient, main: MainClient, catalog: ToolCatalog):

        self.settings = settings
        self.llm      = llm
        self.main     = main
        self.catalog  = catalog


    async def run_turn(self, session_id: str | None, user_text: str) -> TurnResult:
        session_id = history.ensure_session(session_id, user_text)
        history.add_message(session_id, "user", user_text)

        manifests = await self.catalog.select(user_text, always=history.used_tools(session_id))
        by_function = {m["function_name"]: m for m in manifests}
        tools = [to_function(m) for m in manifests] + meta_tools.DEFINITIONS

        result = TurnResult(session_id=session_id, content="")

        for _ in range(self.settings.agent.max_iterations):
            messages = [{"role": "system", "content": system_prompt(date.today())}]
            messages += history.llm_history(session_id, self.settings.agent.history_messages)

            reply = await self.llm.chat(messages, model=self.settings.llm.model_agent, tools=tools)
            tool_calls = _normalize_calls(reply.get("tool_calls") or [])

            message_id = history.add_message(session_id, "assistant", reply["content"], tool_calls=tool_calls or None)

            if not tool_calls:
                result.content = reply["content"] or "Готово."
                return result

            for call in tool_calls:
                output = await self._dispatch(session_id, message_id, call, by_function)
                result.tool_events.append({"tool": call["function"]["name"], "status": output.get("status")})

                history.add_message(
                    session_id,
                    "tool",
                    truncate(output, _RESULT_CHARS),
                    tool_call_id=call["id"],
                    name=call["function"]["name"],
                )

        result.content = "Я сделал много шагов подряд и остановился. Напишите, продолжать ли."
        history.add_message(session_id, "assistant", result.content)

        return result


    async def _dispatch(
        self,
        session_id: str,
        message_id: int,
        call: dict[str, Any],
        by_function: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        name = call["function"]["name"]

        try:
            args = json.loads(call["function"].get("arguments") or "{}")

        except ValueError:
            return {"status": "error", "error": "Аргументы не являются JSON"}

        if not isinstance(args, dict):
            return {"status": "error", "error": "Аргументы должны быть объектом"}

        if name == "action_confirm":
            return await self._confirm(session_id, args)

        if name in meta_tools.NAMES:
            output = await meta_tools.call(self.main, session_id, name, args)
            history.add_trace(session_id, message_id, f"meta:{name}", args, output, output["status"])
            return output

        manifest = by_function.get(name)
        tool = manifest["name"] if manifest else tool_name(name)

        output = await self._execute(session_id, tool, args, confirmed=False)
        trace_id = history.add_trace(session_id, message_id, tool, args, output, output["status"])

        if output["status"] == "needs_approval":
            output["action_id"] = trace_id
            output["next"] = "Опиши пользователю действие и спроси согласие. При «да» вызови action_confirm с этим action_id."

        return output


    async def _confirm(self, session_id: str, args: dict[str, Any]) -> dict[str, Any]:
        action_id = args.get("action_id")
        event = history.get_trace(int(action_id)) if action_id is not None else None

        if event is None or event.session_id != session_id or event.status != "needs_approval":
            return {"status": "error", "error": "Нет действия, ожидающего подтверждения, с таким action_id"}

        output = await self._execute(session_id, event.tool, event.args, confirmed=True)
        history.update_trace(event.id, status="confirmed" if output["status"] == "ok" else output["status"], result=output)

        return output


    async def _execute(self, session_id: str, tool: str, args: dict[str, Any], *, confirmed: bool) -> dict[str, Any]:
        try:
            response = await self.main.post(
                "/executor/execute",
                json={
                    "tool": tool,
                    "args": args,
                    "context": {"mode": "chat", "session_id": session_id, "confirmed": confirmed},
                },
            )

        except ApiError as e:
            return {"status": "error", "error": e.message}

        return {key: value for key, value in response.items() if value not in (None, [], {}) or key == "output"}


def _normalize_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only what the API needs to see again; some servers omit ids or send
    arguments as objects instead of JSON strings."""
    normalized = []

    for i, call in enumerate(calls):
        function = call.get("function") or {}
        arguments = function.get("arguments")

        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False)

        normalized.append(
            {
                "id": call.get("id") or f"call_{i}",
                "type": "function",
                "function": {"name": function.get("name", ""), "arguments": arguments},
            }
        )

    return normalized
