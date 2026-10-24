"""Chat trace → TaskSpec (compile) and instruction → JSON Patch (patch)."""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import date
from difflib import SequenceMatcher
from typing import Any

import jsonpatch
import jsonpointer

from common import tunables
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.llm import LLM
from common.schemas.task import TaskSpec
from common.settings import Settings
from common.storage.db import session
from common import templating
from common.storage.models import ChatMessage, TaskVersion, TraceEvent
from common.utils import truncate
from compiler import prompts, store, task_settings, versions
from compiler.cards import build_cards, describe_trigger, diff_specs, summarize
from compiler.validate import SpecError, validate_spec


log = logging.getLogger("compiler")

_CATALOG_TTL_SEC = 60


class Compiler:
    def __init__(self, settings: Settings, llm: LLM, main: MainClient):

        self.settings = settings
        self.llm      = llm
        self.main     = main

        self._catalog: dict[str, dict[str, Any]] = {}
        self._catalog_at = 0.0
        # Manifests of approved external tool versions: they never change.
        self._versions: dict[tuple[str, int], dict[str, Any] | None] = {}


    async def compile(self, session_id: str, *, title: str | None = None, hint: str | None = None) -> dict[str, Any]:
        catalog = await self.catalog()

        transcript = build_transcript(session_id)
        if not transcript:
            raise ApiError(status_code=404, message=f"Session `{session_id}` has no messages to compile")

        messages = prompts.compile_messages(
            transcript=transcript,
            catalog=catalog,
            spec_schema=TaskSpec.model_json_schema(by_alias=True),
            title=title,
            hint=hint,
            today=date.today(),
        )

        # What a task can redo: writing one's own command (chat only) is not among it.
        chat_tools = successful_tools(session_id) & catalog.keys()
        texts = chat_texts(session_id)
        spec = await self._ask_spec(messages, catalog, chat_tools, texts)

        # Only build and check: nothing runs. The draft goes back to the chat, where the
        # user asks for a dry run, corrections, or approves it.
        cards = build_cards(spec, catalog)
        settings = task_settings.sync({}, spec, catalog)
        version = store.create_task(spec, session_id=session_id, cards=cards, summary=summarize(spec), settings=settings)

        log.info("compiled task %s (%s) from session %s", version.task_id, spec.title, session_id)

        return {
            "task_id": version.task_id,
            "version_id": version.id,
            "title": spec.title,
            "summary": summarize(spec),
            "trigger": describe_trigger(spec.trigger),
            "settings": task_settings.describe(settings, catalog),
            "cards": cards,
        }


    async def _ask_spec(
        self,
        messages: list[dict[str, Any]],
        catalog: dict[str, dict[str, Any]],
        chat_tools: set[str],
        texts: list[str],
    ) -> TaskSpec:
        def validate(data: Any) -> TaskSpec:
            # A new task runs the current version of each external tool.
            if isinstance(data, dict):
                versions.pin(data, catalog, keep={}, explicit=False)

            spec = validate_spec(data, catalog)
            check_reflects_chat(spec, chat_tools)
            check_verbatim(spec, texts)
            return spec

        # Compiling is multi-step reasoning over the whole chat, so it uses the chat model.
        return await self.llm.chat.structured(
            messages,
            schema=TaskSpec.model_json_schema(by_alias=True),
            name="task_spec",
            validate=validate,
            attempts=tunables.get("compiler_max_attempts"),
        )


    async def patch(self, task_id: str, instruction: str, *, session_id: str | None = None) -> dict[str, Any]:
        base = store.base_version(task_id)
        kept = versions.pins(base.spec)
        catalog = await self.catalog_for(kept)

        messages = prompts.patch_messages(spec=base.spec, instruction=instruction, catalog=catalog, today=date.today())

        def validate(data: Any) -> tuple[dict[str, Any], TaskSpec]:
            try:
                patched = jsonpatch.apply_patch(base.spec, data["ops"])

            except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
                raise ValueError(f"JSON Patch не применяется: {e}") from e

            # The model keeps the task's tool versions, whatever it wrote.
            versions.pin(patched, catalog, keep=kept, explicit=False)

            return data, validate_spec(patched, catalog)

        result, spec = await self.llm.chat.structured(
            messages,
            schema=prompts.PATCH_SCHEMA,
            name="task_patch",
            validate=validate,
            attempts=tunables.get("compiler_max_attempts"),
        )

        return self._new_version(
            task_id,
            base,
            spec,
            catalog,
            patch=result["ops"],
            summary=result.get("summary"),
            created_by="patch",
            session_id=session_id,
        )


    async def edit(self, task_id: str, *, spec: dict[str, Any] | None, ops: list[dict[str, Any]] | None, summary: str | None) -> dict[str, Any]:
        """A user's own edit, no LLM: a whole spec or a JSON Patch to the current one.
        Same checks as a compiled task; the result is a draft for the user to approve."""
        if (spec is None) == (ops is None):
            raise ApiError(status_code=422, message="Нужно одно из двух: spec (задача целиком) или ops (JSON Patch)")

        base = store.base_version(task_id)
        base_catalog = await self.catalog_for(versions.pins(base.spec))

        if ops is not None:
            try:
                spec = jsonpatch.apply_patch(base.spec, ops)

            except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
                raise ApiError(status_code=422, message=f"JSON Patch не применяется: {e}") from e

        # The user's own edit may name another version; the rest stays as the task had it.
        versions.pin(spec, base_catalog, keep=versions.pins(base.spec), explicit=True)
        catalog = await self.catalog_for(versions.pins(spec))

        try:
            checked = validate_spec(spec, catalog)

        except SpecError as e:
            raise ApiError(status_code=422, message="Задача не прошла проверку", details={"errors": e.errors}) from e

        return self._new_version(
            task_id, base, checked, catalog, base_catalog=base_catalog, patch=ops, summary=summary, created_by="manual", session_id=None
        )


    async def move_to_tool_version(self, task_id: str, tool: str, version: int | None) -> dict[str, Any]:
        """A draft where every step of `tool` runs `version` (the current one if None);
        checked against that version's schemas like any edit."""
        base = store.base_version(task_id)
        # Fresh: the version just approved must count as current, cache or not.
        current = (await self.catalog(fresh=True)).get(tool, {}).get("version")
        target = version or current

        if target is None:
            raise ApiError(status_code=404, message=f"У `{tool}` нет одобренной версии")

        spec = copy.deepcopy(base.spec)
        steps = [step for step in versions.tool_steps(spec) if step["tool"] == tool]

        if not steps:
            raise ApiError(status_code=422, message=f"В задаче нет шагов с `{tool}`")

        before = steps[0].get("tool_version")
        if all(step.get("tool_version") == target for step in steps):
            raise ApiError(status_code=422, message=f"Задача уже на `{tool}` v{target}")

        for step in steps:
            step["tool_version"] = target

        try:
            return await self.edit(task_id, spec=spec, ops=None, summary=f"{tool}: v{before} → v{target}")

        except ApiError as e:
            if e.status_code == 422 and e.details:
                e.message = f"Шаги задачи не подходят к `{tool}` v{target}: поправьте задачу словами"
            raise


    def _new_version(
        self,
        task_id: str,
        base: TaskVersion,
        spec: TaskSpec,
        catalog: dict[str, dict[str, Any]],
        *,
        base_catalog: dict[str, dict[str, Any]] | None = None,
        patch: list[dict[str, Any]] | None,
        summary: str | None,
        created_by: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        cards = build_cards(spec, catalog)
        diff = diff_specs(TaskSpec.model_validate(base.spec), spec, base_catalog or catalog, catalog)

        if not diff["changes"]:
            raise ApiError(status_code=422, message="Изменение ничего не меняет в задаче")

        version = store.add_version(
            task_id,
            spec,
            parent=base,
            patch=patch,
            cards=cards,
            summary=summary or summarize(spec),
            created_by=created_by,
            session_id=session_id,
        )

        return {
            "task_id": task_id,
            "version_id": version.id,
            "title": spec.title,
            "summary": version.summary,
            "diff": diff["lines"],
            "changes": diff["changes"],
            "cards": cards,
        }


    async def diff(self, task_id: str, version_id: str) -> dict[str, Any]:
        version = store.get_version(task_id, version_id)
        if not version.parent_version_id:
            return {"changes": [], "lines": ["Первая версия задачи"]}

        parent = store.get_version(task_id, version.parent_version_id)

        return diff_specs(
            TaskSpec.model_validate(parent.spec),
            TaskSpec.model_validate(version.spec),
            await self.catalog_for(versions.pins(parent.spec)),
            await self.catalog_for(versions.pins(version.spec)),
        )


    async def catalog(self, *, fresh: bool = False) -> dict[str, dict[str, Any]]:
        if fresh or not self._catalog or time.monotonic() - self._catalog_at > _CATALOG_TTL_SEC:
            data = await self.main.get("/tools")
            # A task never has a chat-only tool as a step: the compiler does not see them.
            self._catalog = {tool["name"]: tool for tool in data["tools"] if not tool.get("chat_only")}
            self._catalog_at = time.monotonic()

        return self._catalog


    async def catalog_for(self, pinned: dict[str, int]) -> dict[str, dict[str, Any]]:
        """The catalog as a task with these external tool versions sees it."""
        return await versions.overlay(await self.catalog(), pinned, self._version_manifest)


    async def _version_manifest(self, tool: str, version: int) -> dict[str, Any] | None:
        if (tool, version) not in self._versions:
            try:
                self._versions[(tool, version)] = await self.main.get(f"/tools/{tool}", params={"version": version})

            except ApiError as e:
                if e.status_code != 404:
                    raise

                return None

        return self._versions[(tool, version)]


def successful_tools(session_id: str) -> set[str]:
    with session() as db:
        rows = (
            db.query(TraceEvent.tool)
            .filter(TraceEvent.session_id == session_id, TraceEvent.status.in_(("ok", "confirmed")))
            .distinct()
            .all()
        )

    return {tool for (tool,) in rows if not tool.startswith("meta:")}


def check_reflects_chat(spec: TaskSpec, chat_tools: set[str]) -> None:
    """A compiled task must redo at least one thing the chat actually did; a spec of
    only notifications means the model lost the conversation."""
    used = {step.tool for step in spec.all_steps() if step.tool}
    substantive = chat_tools - {"user.notify"}

    if substantive and not used & substantive:
        raise SpecError([
            "задача не повторяет ни одного действия из разговора; "
            f"в разговоре успешно выполнялись: {sorted(substantive)}"
        ])


# Shorter strings (names, folders, short subjects) are too close to each other by chance.
_VERBATIM_MIN_LEN = 15
# A literal sharing this much with a chat text but not equal to any part of it is a retyped copy.
_NEAR_COPY = 0.9


def chat_texts(session_id: str) -> list[str]:
    """What the user wrote and what the chat's successful tool calls were given."""
    with session() as db:
        messages = db.query(ChatMessage.content).filter(ChatMessage.session_id == session_id, ChatMessage.role == "user").all()
        events = (
            db.query(TraceEvent.tool, TraceEvent.args)
            .filter(TraceEvent.session_id == session_id, TraceEvent.status.in_(("ok", "needs_approval", "confirmed")))
            .all()
        )

    texts = [content for (content,) in messages if content]
    texts += [s for tool, args in events if not tool.startswith("meta:") for s in _literals(args)]

    return texts


def check_verbatim(spec: TaskSpec, texts: list[str]) -> None:
    """Texts the user gave must be copied, not retyped: a near-copy with a slip (a lost
    letter, a changed word) would go out in every run."""
    errors: list[str] = []
    values = [*spec.variables.values(), *(s for step in spec.all_steps() for s in _literals(step.args))]

    for value in values:
        if len(value) < _VERBATIM_MIN_LEN or templating.has_template(value) or any(value in text for text in texts):
            continue

        for text in texts:
            original = _near_copy_of(value, text)
            if original is not None:
                errors.append(f"текст «{value}» переписан с ошибками; в разговоре было «{original}» — перенеси дословно")
                break

    if errors:
        raise SpecError(errors)


def _near_copy_of(value: str, text: str) -> str | None:
    blocks = [b for b in SequenceMatcher(None, value, text, autojunk=False).get_matching_blocks() if b.size]
    if not blocks or sum(b.size for b in blocks) < _NEAR_COPY * len(value):
        return None

    # Letters scattered over a long message are not a copy: the match must be one passage.
    original = text[blocks[0].b : blocks[-1].b + blocks[-1].size]
    return original if len(original) <= len(value) / _NEAR_COPY else None


def _literals(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return [s for item in value for s in _literals(item)]

    if isinstance(value, dict):
        return [s for item in value.values() for s in _literals(item)]

    return []


def build_transcript(session_id: str) -> str:
    """The chat as the compiler sees it: messages plus each tool call and its result."""
    with session() as db:
        messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id).all()
        trace = db.query(TraceEvent).filter(TraceEvent.session_id == session_id).order_by(TraceEvent.id).all()

    calls_by_message: dict[int | None, list[TraceEvent]] = {}
    for event in trace:
        if not event.tool.startswith("meta:"):
            calls_by_message.setdefault(event.message_id, []).append(event)

    lines: list[str] = []

    for message in messages:
        if message.role == "user":
            lines.append(f"Пользователь: {message.content}")

        elif message.role == "assistant":
            if message.content:
                lines.append(f"Ассистент: {message.content}")

            for event in calls_by_message.get(message.id, []):
                result = event.result or {}
                payload = result.get("output") if result.get("status") in ("ok", "needs_approval") else result.get("error")
                lines.append(
                    f"→ {event.tool}({json.dumps(event.args, ensure_ascii=False)}) "
                    f"[{event.status}]: {truncate(payload, 800)}"
                )

    return "\n".join(lines)
