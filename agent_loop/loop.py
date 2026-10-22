"""One chat turn: history → tools → LLM → tool calls through main → … → answer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from jsonschema import Draft202012Validator

from agent_loop import history, meta_tools
from agent_loop.catalog import ToolCatalog, to_function
from agent_loop.prompts import system_prompt
from common.tools.guides import collect
from common import tunables
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.llm import LLM, LLMNotConfigured
from common.settings import Settings
from common.tools import tool_name
from common.utils import truncate


log = logging.getLogger("loop")

# Tool results are cut before they go back into the context window.
_RESULT_CHARS = 6000

_MODEL_ATTEMPTS = 3

_NUDGE = "Ты остановился без ответа. Продолжи работу инструментами или коротко сообщи пользователю результат."


@dataclass(slots=True)
class TurnResult:
    session_id: str
    content: str
    tool_events: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    def __init__(self, settings: Settings, llm: LLM, main: MainClient, catalog: ToolCatalog):

        self.settings = settings
        self.llm      = llm
        self.main     = main
        self.catalog  = catalog

        # Commands the user said to stop asking about, per chat. Memory only: a short-lived
        # fact of one conversation, gone with a restart (the chat simply asks again).
        self._consent: dict[str, set[str]] = {}


    def forget(self, session_ids: list[str]) -> None:
        """Deleted chats take their consents along."""
        for session_id in session_ids:
            self._consent.pop(session_id, None)


    async def run_turn(self, session_id: str | None, user_text: str) -> TurnResult:
        session_id = history.ensure_session(session_id, user_text)
        history.add_message(session_id, "user", user_text)

        # Without a model there is no turn; the answer says how to connect one.
        if self.llm.chat.endpoint() is None:
            content = LLMNotConfigured("chat").message
            history.add_message(session_id, "assistant", content)
            return TurnResult(session_id=session_id, content=content)

        manifests = await self.catalog.select(user_text, always=history.used_tools(session_id))
        by_function = {m["function_name"]: m for m in manifests}
        tools = [to_function(m) for m in manifests] + meta_tools.DEFINITIONS

        result = TurnResult(session_id=session_id, content="")
        nudged = False

        for _ in range(tunables.get("agent_max_iterations")):
            messages = [{"role": "system", "content": system_prompt(date.today(), collect(manifests, "agent"))}]
            messages += history.llm_history(session_id, tunables.get("agent_history_messages"))

            # Turn-local only: the nudge never becomes part of the stored chat.
            if nudged:
                messages.append({"role": "user", "content": _NUDGE})

            reply = await self._complete(messages, tools)
            tool_calls = _normalize_calls(reply.get("tool_calls") or [])

            # An empty reply with no calls means the model stalled mid-task; ask once more.
            if not tool_calls and not reply["content"] and not nudged:
                nudged = True
                continue

            message_id = history.add_message(session_id, "assistant", reply["content"], tool_calls=tool_calls or None)

            if not tool_calls:
                result.content = reply["content"] or "Не получилось завершить работу. Попробуйте переформулировать просьбу."
                return result

            for call in tool_calls:
                output = await self._dispatch(session_id, message_id, call, by_function)
                event = {"tool": tool_name(call["function"]["name"]), "status": output.get("status")}
                result.tool_events.append(event)

                # Lets a UI show progress while a long turn is still running.
                await self.main.publish("agent.tool", {"session_id": session_id, **event})

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


    async def _complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        """One model step. Ollama answers 500 when a model emits a malformed tool call;
        sampling again usually fixes it."""
        for attempt in range(1, _MODEL_ATTEMPTS + 1):
            try:
                return await self.llm.chat.complete(messages, tools=tools)

            except ApiError as e:
                if e.status_code != 502 or attempt == _MODEL_ATTEMPTS:
                    raise

                log.warning("model step failed (%s), retrying %d/%d", e.message, attempt, _MODEL_ATTEMPTS)

        raise AssertionError("unreachable")


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

        tool = tool_name(name)
        # The whole catalog, not just this turn's selection: an external tool must never
        # slip past the question because the model reached it some other way.
        manifest = by_function.get(name) or (await self.catalog.manifests()).get(tool)

        # Asking first is the chat's rule, not the executor's: the user is right here. The
        # call waits in the trace until action_confirm. A command that does not say it is
        # harmless (confirm=False) asks, unless the user let this chat stop asking about it.
        if manifest is not None and self._needs_asking(session_id, manifest):
            return self._ask_first(session_id, message_id, manifest, args)

        output = await self._execute(session_id, tool, args)
        history.add_trace(session_id, message_id, tool, args, output, output["status"])

        return output


    def _needs_asking(self, session_id: str, manifest: dict[str, Any]) -> bool:
        confirm = manifest.get("confirm", True)

        if confirm is False:
            return False

        return confirm == "always" or manifest["name"] not in self._consent.get(session_id, set())


    def _ask_first(self, session_id: str, message_id: int, manifest: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
        # A call that cannot run anyway is reported now, not after the user said yes.
        problems = [e.message for e in Draft202012Validator(manifest["input_schema"]).iter_errors(args)]
        if problems:
            output = {"status": "error", "error": f"Неверные аргументы `{manifest['name']}`: {'; '.join(problems)}"}
            history.add_trace(session_id, message_id, manifest["name"], args, output, "error")
            return output

        output = {
            "status": "needs_approval",
            "message": f"«{manifest['title']}» требует подтверждения пользователя",
            "preview": {"tool": manifest["name"], "title": manifest["title"], "args": args},
        }
        trace_id = history.add_trace(session_id, message_id, manifest["name"], args, output, "needs_approval")

        if manifest.get("confirm") == "always":
            options = "При «да» вызови action_confirm с этим action_id. Эта команда спрашивает каждый раз."
        else:
            options = (
                f"Предложи ответить «да» или «да, и больше не спрашивай про «{manifest['title']}» в этом чате». "
                "При «да» вызови action_confirm с этим action_id; при втором ответе — ещё и с remember=true. "
                "remember=true — только если пользователь сам так сказал."
            )

        return {
            **output,
            "action_id": trace_id,
            "next": (
                f"Опиши пользователю действие и спроси согласие. {options} "
                "Шаги, которые имеют смысл только после этого действия, до ответа пользователя не делай — "
                "сделай их после action_confirm."
            ),
        }


    async def _confirm(self, session_id: str, args: dict[str, Any]) -> dict[str, Any]:
        action_id = args.get("action_id")
        event = history.get_trace(int(action_id)) if action_id is not None else None

        if event is None or event.session_id != session_id or event.status != "needs_approval":
            return {"status": "error", "error": "Нет действия, ожидающего подтверждения, с таким action_id"}

        # The user's yes is a message of theirs after the question, not the model's word for
        # it: text the model has read (a letter, a document) cannot confirm on their behalf.
        if not history.user_wrote_after(session_id, event.message_id):
            return {
                "status": "error",
                "error": "Пользователь ещё не ответил. Опиши ему действие, закончи ответ и дождись его «да».",
            }

        if args.get("remember") is True:
            manifest = (await self.catalog.manifests()).get(event.tool) or {}

            # A command that asks every time (sending mail) cannot be let off.
            if manifest.get("confirm") is True:
                self._consent.setdefault(session_id, set()).add(event.tool)

        output = await self._execute(session_id, event.tool, event.args)
        history.update_trace(event.id, status="confirmed" if output["status"] == "ok" else output["status"], result=output)

        return output


    async def _execute(self, session_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.main.post(
                "/executor/execute",
                json={
                    "tool": tool,
                    "args": args,
                    "context": {"mode": "chat", "session_id": session_id},
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
