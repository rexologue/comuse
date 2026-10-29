"""One chat turn: history → tools → LLM → tool calls through main → … → answer."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
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
from common.utils import shrink


log = logging.getLogger("loop")

# Tool results are cut before they go back into the context window.
_RESULT_CHARS = 6000

# A reply this short («да», «нет», «продолжай») says little by itself: the tools for the
# turn are chosen by what it answers too.
_SHORT_REPLY_CHARS = 80

# An answer that starts with a no is not a yes, whatever the model makes of it.
_REFUSAL = re.compile(r"^\W*(нет|не|отмен\w*|стоп|no|cancel)\b", re.IGNORECASE)

_CUT = (
    "Результат длиннее, чем помещается, и сокращён: помеченного «обрезано» ты не видел. Длинный текст читай "
    "частями поменьше (max_chars), следующую — с того места, докуда видел."
)

_MODEL_ATTEMPTS = 3

# The shortest history a model is tried with before its error is the turn's.
_HISTORY_CHARS_LEAST = 2000

_STALLED = "Не получилось завершить работу. Попробуйте переформулировать просьбу."
_LIMIT = (
    "Шаги на этот ход закончились, инструменты больше не вызывай. Коротко скажи пользователю: что из просьбы "
    "сделано (по результатам вызовов выше), что не сделано и почему, и спроси, продолжать ли."
)
_LIMIT_FALLBACK = "Я сделал много шагов подряд и остановился: {done}. Напишите, продолжать ли."
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

        # One turn at a time per chat: a message sent while the agent still works waits
        # for that turn to end, so two turns never write into one history at once.
        self._turns: dict[str, asyncio.Lock] = {}


    def forget(self, session_ids: list[str]) -> None:
        """Deleted chats take their consents along."""
        for session_id in session_ids:
            self._consent.pop(session_id, None)

            lock = self._turns.get(session_id)
            if lock is not None and not lock.locked():
                del self._turns[session_id]


    def busy(self, session_id: str) -> bool:
        """A turn runs in the chat now (or waits for the one before)."""
        lock = self._turns.get(session_id)
        return lock is not None and lock.locked()


    async def run_turn(self, session_id: str | None, user_text: str) -> TurnResult:
        session_id = history.ensure_session(session_id, user_text)

        async with self._turns.setdefault(session_id, asyncio.Lock()):
            return await self._turn(session_id, user_text)


    async def _turn(self, session_id: str, user_text: str) -> TurnResult:
        # Written only now: a turn still running reads the history from the latest user
        # message on, and must not find this one there.
        history.add_message(session_id, "user", user_text)

        # Without a model there is no turn; the answer says how to connect one.
        if self.llm.chat.endpoint() is None:
            content = LLMNotConfigured("chat").message
            history.add_message(session_id, "assistant", content)
            return TurnResult(session_id=session_id, content=content)

        query = user_text if len(user_text) >= _SHORT_REPLY_CHARS else f"{history.answered_text(session_id)}\n{user_text}"
        manifests = await self.catalog.select(query, always=history.used_tools(session_id))
        by_function = {m["function_name"]: m for m in manifests}
        tools = [to_function(m) for m in manifests] + meta_tools.DEFINITIONS

        result = TurnResult(session_id=session_id, content="")
        nudged = False
        # The calls of this turn that failed, by what was called: the same call again
        # would fail the same way, and a turn of fifteen such calls says nothing.
        failed: dict[str, str] = {}

        for _ in range(tunables.get("agent_max_iterations")):
            # Turn-local only: the nudge never becomes part of the stored chat.
            reply = await self._step(session_id, manifests, tools, [{"role": "user", "content": _NUDGE}] if nudged else [])
            tool_calls = _normalize_calls(reply.get("tool_calls") or [])

            # An empty reply with no calls means the model stalled mid-task; ask once more.
            if not tool_calls and not reply["content"] and not nudged:
                nudged = True
                continue

            # What the client gets is what is stored: the client's copy of the chat must
            # match ours for a chat without session_id to be found again.
            content = reply["content"] or ("" if tool_calls else _STALLED)
            message_id = history.add_message(session_id, "assistant", content, tool_calls=tool_calls or None)

            if not tool_calls:
                result.content = content
                return result

            for call in tool_calls:
                # A tool the model reached outside this turn's selection joins it: its next
                # calls come with the schema, not guessed.
                if (added := await self._offer(call, by_function)) is not None:
                    manifests.append(added)
                    tools.insert(len(tools) - len(meta_tools.DEFINITIONS), to_function(added))

                key = f"{call['function']['name']}:{call['function'].get('arguments') or ''}"
                if key in failed:
                    output = {
                        "status": "error",
                        "error": (
                            f"Этот вызов с теми же аргументами уже не удался: {failed[key]}. Не повторяй его: "
                            "измени аргументы, если знаешь как, или скажи пользователю, что не получилось и почему."
                        ),
                    }
                else:
                    try:
                        output = await self._dispatch(session_id, message_id, call, by_function)

                    except Exception as e:
                        # Every call gets its result, or the chat could not go on.
                        log.exception("tool call %s failed", call["function"]["name"])
                        output = {"status": "error", "error": f"Сбой: {e}"}

                    if output.get("status") == "error":
                        failed[key] = str(output.get("error"))[:300]

                event = {"tool": tool_name(call["function"]["name"]), "status": output.get("status")}
                result.tool_events.append(event)

                # Lets a UI show progress while a long turn is still running.
                await self.main.publish("agent.tool", {"session_id": session_id, **event})

                history.add_message(
                    session_id,
                    "tool",
                    _shown(output),
                    tool_call_id=call["id"],
                    name=call["function"]["name"],
                )

        result.content = await self._limit_answer(session_id, manifests, result)
        history.add_message(session_id, "assistant", result.content)

        return result


    async def _limit_answer(self, session_id: str, manifests: list[dict[str, Any]], result: TurnResult) -> str:
        """Out of steps: the model says, without tools, what got done and what did not —
        not a bare «stopped» after a dozen calls the user cannot see."""
        try:
            reply = await self._step(session_id, manifests, [], [{"role": "user", "content": _LIMIT}])
            if reply.get("content") and not reply.get("tool_calls"):
                return reply["content"]

        except ApiError as e:
            log.warning("limit answer failed: %s", e.message)

        ok = sum(1 for event in result.tool_events if event["status"] in ("ok", "confirmed"))
        errors = sum(1 for event in result.tool_events if event["status"] == "error")

        return _LIMIT_FALLBACK.format(done=f"вызовов — {len(result.tool_events)}, удачных — {ok}, с ошибкой — {errors}")


    async def _offer(self, call: dict[str, Any], by_function: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        name = call["function"]["name"]
        if name in by_function or name in meta_tools.NAMES or name == "action_confirm":
            return None

        manifest = (await self.catalog.manifests()).get(tool_name(name))
        if manifest is not None:
            by_function[name] = manifest

        return manifest


    async def _step(
        self,
        session_id: str,
        manifests: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        extra: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """One model step on the chat as much of it fits: a server that finds it too long
        for its context gets it again, shorter, instead of the chat breaking for good."""
        chars = tunables.get("agent_history_chars")

        while True:
            messages = [{"role": "system", "content": system_prompt(datetime.now().astimezone(), collect(manifests, "agent"))}]
            messages += history.llm_history(session_id, chars) + extra

            try:
                return await self._complete(messages, tools)

            except ApiError as e:
                if not _too_long(e) or chars <= _HISTORY_CHARS_LEAST:
                    raise

                chars //= 2
                log.warning("chat %s too long for the model, history cut to %d characters", session_id, chars)


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
            if asking := meta_tools.manifest(name):
                return self._ask_first(session_id, message_id, asking, args)

            output = await meta_tools.call(self.main, session_id, name, args)
            history.add_trace(session_id, message_id, f"meta:{name}", args, output, output["status"])
            return output

        tool = tool_name(name)
        # The whole catalog, not just this turn's selection: an external tool must never
        # slip past the question because the model reached it some other way. One approved
        # a moment ago is not in the cached catalog yet: a miss reloads it, and what is
        # still unknown does not run at all.
        manifest = (
            by_function.get(name)
            or (await self.catalog.manifests()).get(tool)
            or (await self.catalog.manifests(fresh=True)).get(tool)
        )
        if manifest is None:
            output = {"status": "error", "error": f"Нет команды `{tool}`"}
            history.add_trace(session_id, message_id, tool, args, output, "error")
            return output

        # Asking first is the chat's rule, not the executor's: the user is right here. The
        # call waits in the trace until action_confirm. A command that does not say it is
        # harmless (confirm=False) asks, unless the user let this chat stop asking about it.
        if self._needs_asking(session_id, manifest):
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
        reply = history.user_reply_after(session_id, event.message_id)
        if reply is None:
            return {
                "status": "error",
                "error": "Пользователь ещё не ответил. Опиши ему действие, закончи ответ и дождись его «да».",
            }

        # Whether the answer is a yes is the model's reading; a plain no is not left to it.
        if _REFUSAL.match(reply):
            return {
                "status": "error",
                "error": "Пользователь отказался: действие не выполнено. Не делай его; если он захочет иначе — спроси заново.",
            }

        note = None
        if args.get("remember") is True:
            manifest = (await self.catalog.manifests()).get(event.tool) or {}

            # A command that asks every time (sending mail) cannot be let off.
            if manifest.get("confirm") is True:
                self._consent.setdefault(session_id, set()).add(event.tool)
            else:
                note = "Эта команда спрашивает каждый раз: не обещай пользователю, что больше не спросишь"

        if event.tool.startswith("meta:"):
            output = await meta_tools.call(self.main, session_id, event.tool.removeprefix("meta:"), event.args)
        else:
            output = await self._execute(session_id, event.tool, event.args)

        history.update_trace(event.id, status="confirmed" if output["status"] == "ok" else output["status"], result=output)

        return {**output, "note": note} if note else output


    async def _execute(self, session_id: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.main.call_tool(tool, args, context={"mode": "chat", "session_id": session_id})

        except ApiError as e:
            return {"status": "error", "error": e.message}

        return {key: value for key, value in response.items() if value not in (None, [], {}) or key == "output"}


def _shown(output: dict[str, Any]) -> str:
    """The result as the model gets it. A cut one says so where the model looks first: a
    text read in windows of 20000 and shown 6000 would otherwise go on from 20000."""
    text = shrink(output, _RESULT_CHARS)
    if text == json.dumps(output, ensure_ascii=False, default=str):
        return text

    return shrink({"note": _CUT, **output}, _RESULT_CHARS)


def _too_long(error: ApiError) -> bool:
    """The server's own words: every one names its context when a request does not fit."""
    return error.status_code in (400, 413) and "context" in json.dumps(error.details or {}, ensure_ascii=False).lower()


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
