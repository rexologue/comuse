"""Chat trace → TaskSpec (compile) and instruction → JSON Patch (patch)."""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

import jsonpatch
import jsonpointer

from common.clients.llm_client import LLMClient
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.settings import Settings
from common.storage.db import session
from common.storage.models import ChatMessage, TraceEvent
from common.utils import truncate
from compiler import prompts, store
from compiler.cards import build_cards, describe_trigger, diff_specs, summarize
from compiler.permissions import derive_permissions
from compiler.validate import SpecError, validate_spec


log = logging.getLogger("compiler")

_CATALOG_TTL_SEC = 60

_DRY_RUN_REPAIRS = 2


class Compiler:
    def __init__(self, settings: Settings, llm: LLMClient, main: MainClient):

        self.settings = settings
        self.llm      = llm
        self.main     = main

        self._catalog: dict[str, dict[str, Any]] = {}
        self._catalog_at = 0.0


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

        chat_tools = successful_tools(session_id)
        spec = await self._ask_spec(messages, catalog, chat_tools)
        version = None

        # A spec that validates can still fail on real data; show the model what the
        # dry run hit and let it repair the spec a couple of times.
        for repair in range(_DRY_RUN_REPAIRS + 1):
            spec = spec.model_copy(update={"permissions": derive_permissions(spec, catalog)})
            cards = build_cards(spec, catalog)

            if version is None:
                version = store.create_task(spec, session_id=session_id, cards=cards, summary=summarize(spec))
            else:
                store.replace_draft(version.id, spec, cards=cards, summary=summarize(spec))

            dry_run = await self._dry_run(version.id)
            store.set_dry_run(version.id, dry_run)

            if dry_run["status"] != "failed" or repair == _DRY_RUN_REPAIRS:
                break

            log.info("dry run of %s failed (%s), repairing", version.id, dry_run.get("error"))
            messages = messages + [
                {"role": "assistant", "content": json.dumps(spec.dump(), ensure_ascii=False)},
                {"role": "user", "content": prompts.repair_request(dry_run)},
            ]
            spec = await self._ask_spec(messages, catalog, chat_tools)

        log.info("compiled task %s (%s) from session %s, dry run %s", version.task_id, spec.title, session_id, dry_run["status"])

        return {
            "task_id": version.task_id,
            "version_id": version.id,
            "title": spec.title,
            "summary": summarize(spec),
            "trigger": describe_trigger(spec.trigger),
            "permissions": spec.permissions.model_dump(),
            "cards": cards,
            "dry_run": dry_run,
        }


    async def _ask_spec(
        self,
        messages: list[dict[str, Any]],
        catalog: dict[str, dict[str, Any]],
        chat_tools: set[str],
    ) -> TaskSpec:
        def validate(data: Any) -> TaskSpec:
            spec = validate_spec(data, catalog)
            check_reflects_chat(spec, chat_tools)
            return spec

        # Compiling is multi-step reasoning over the whole chat, so it uses the chat model.
        return await self.llm.structured(
            messages,
            model=self.settings.llm.model_chat,
            schema=TaskSpec.model_json_schema(by_alias=True),
            name="task_spec",
            validate=validate,
            attempts=self.settings.compiler.max_attempts,
            reasoning_effort=self.settings.llm.chat_reasoning_effort or None,
        )


    async def patch(self, task_id: str, instruction: str) -> dict[str, Any]:
        catalog = await self.catalog()
        base = store.base_version(task_id)

        messages = prompts.patch_messages(spec=base.spec, instruction=instruction, catalog=catalog, today=date.today())

        def validate(data: Any) -> tuple[dict[str, Any], TaskSpec]:
            try:
                patched = jsonpatch.apply_patch(base.spec, data["ops"])

            except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
                raise ValueError(f"JSON Patch не применяется: {e}") from e

            return data, validate_spec(patched, catalog)

        result, spec = await self.llm.structured(
            messages,
            model=self.settings.llm.model_chat,
            schema=prompts.PATCH_SCHEMA,
            name="task_patch",
            validate=validate,
            attempts=self.settings.compiler.max_attempts,
            reasoning_effort=self.settings.llm.chat_reasoning_effort or None,
        )

        spec = spec.model_copy(update={"permissions": derive_permissions(spec, catalog)})
        cards = build_cards(spec, catalog)
        diff = diff_specs(TaskSpec.model_validate(base.spec), spec, catalog)

        if not diff["changes"]:
            raise ApiError(status_code=422, message="Изменение ничего не меняет в задаче", details={"instruction": instruction})

        version = store.add_version(
            task_id,
            spec,
            parent=base,
            patch=result["ops"],
            cards=cards,
            summary=result.get("summary") or summarize(spec),
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

        catalog = await self.catalog()
        parent = store.get_version(task_id, version.parent_version_id)

        return diff_specs(TaskSpec.model_validate(parent.spec), TaskSpec.model_validate(version.spec), catalog)


    async def catalog(self) -> dict[str, dict[str, Any]]:
        if not self._catalog or time.monotonic() - self._catalog_at > _CATALOG_TTL_SEC:
            data = await self.main.get("/tools")
            self._catalog = {tool["name"]: tool for tool in data["tools"]}
            self._catalog_at = time.monotonic()

        return self._catalog


    async def _dry_run(self, version_id: str) -> dict[str, Any]:
        """Run the draft with side effects suppressed so the user sees a real result."""
        try:
            run = await self.main.post(
                "/runner/runs",
                json={"task_version_id": version_id, "dry_run": True, "wait": True, "trigger": "dry_run"},
            )

        except ApiError as e:
            log.warning("dry run of %s failed to start: %s", version_id, e.message)
            return {"status": "not_run", "error": e.message}

        return {
            "run_id": run["id"],
            "status": run["status"],
            "error": run.get("error"),
            "steps": [
                {
                    "step": step["step_key"],
                    "status": step["status"],
                    "error": step["error"],
                    "output": truncate(step["output"], 600) if step["output"] is not None else None,
                }
                for step in run.get("steps", [])
            ],
        }


def successful_tools(session_id: str) -> set[str]:
    with session() as db:
        rows = (
            db.query(TraceEvent.tool)
            .filter(TraceEvent.session_id == session_id, TraceEvent.status.in_(("ok", "confirmed")))
            .distinct()
            .all()
        )

    return {tool for (tool,) in rows if not tool.startswith("meta:")}


# A trigger does the looking-around a chat did by hand: a new_email task receives
# the messages mail.search found in chat, a file_appeared task the files fs.list found.
_TRIGGER_COVERS = {
    "new_email": {"mail.search", "mail.read"},
    "file_appeared": {"fs.list"},
}


def check_reflects_chat(spec: TaskSpec, chat_tools: set[str]) -> None:
    """A compiled task must redo at least one thing the chat actually did; a spec of
    only notifications means the model lost the conversation."""
    used = {step.tool for step in spec.all_steps() if step.tool}
    used |= _TRIGGER_COVERS.get(spec.trigger.type, set())
    substantive = chat_tools - {"user.notify", "user.ask"}

    if substantive and not used & substantive:
        raise SpecError([
            "задача не повторяет ни одного действия из разговора; "
            f"в разговоре успешно выполнялись: {sorted(substantive)}"
        ])


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
