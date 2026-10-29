"""Chat trace → TaskSpec (compile) and instruction → JSON Patch (patch)."""

from __future__ import annotations

import logging
from datetime import date
from difflib import SequenceMatcher
from typing import Any

import jsonpatch
import jsonpointer

from common import tunables
from common.clients.catalog import CatalogCache
from common.clients.main_client import MainClient
from common.errors import ApiError
from common.llm import LLM
from common.schemas.task import TaskSpec
from common.settings import Settings
from common import templating
from common.storage.models import TaskVersion
from common.tools.definition import differences, down, snapshot
from common.utils import strings_in
from compiler import prompts, store, task_settings
from compiler.cards import build_cards, describe_trigger, diff_specs, summarize
from compiler.validate import SpecError, validate_spec


log = logging.getLogger("compiler")


class Compiler:
    def __init__(self, settings: Settings, llm: LLM, main: MainClient):

        self.settings = settings
        self.llm      = llm
        self.main     = main

        # A task never has a chat-only tool as a step: the compiler does not see them.
        self._catalog = CatalogCache(main, keep=lambda manifest: not manifest.get("chat_only"))


    async def compile(self, session_id: str, *, title: str | None = None, hint: str | None = None) -> dict[str, Any]:
        # As it is now: a command approved a moment ago in the chat is one the task may call.
        catalog = await self.catalog(fresh=True)

        # The chat is agent-loop's: it says what was said and done there.
        chat = await self.main.get(f"/agent/sessions/{session_id}/transcript")
        transcript = chat["text"]
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
        chat_tools = set(chat["done_tools"]) & catalog.keys()
        texts = chat["given"]
        spec = await self._ask_spec(messages, catalog, chat_tools, texts)

        # Only build and check: nothing runs. The draft goes back to the chat, where the
        # user asks for a dry run, corrections, or approves it.
        cards = build_cards(spec, catalog)
        settings = task_settings.sync({}, spec, catalog)
        version = store.create_task(
            spec, session_id=session_id, cards=cards, summary=summarize(spec), settings=settings, tools=snapshot(spec.tools(), catalog)
        )

        log.info("compiled task %s (%s) from session %s", version.task_id, spec.title, session_id)

        return {
            "task_id": version.task_id,
            "version_id": version.id,
            "title": spec.title,
            "summary": summarize(spec),
            "description": spec.description,
            "trigger": describe_trigger(spec.trigger),
            "settings": task_settings.lines(task_settings.view(settings, spec, catalog, cards)),
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
        catalog = await self.catalog(fresh=True)

        messages = prompts.patch_messages(spec=base.spec, instruction=instruction, catalog=catalog, today=date.today())

        def validate(data: Any) -> tuple[dict[str, Any], TaskSpec]:
            try:
                patched = apply_ops(base.spec, data["ops"])

            except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
                raise ValueError(f"JSON Patch не применяется: {e}; шаги задачи: {step_paths(base.spec)}") from e

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
        catalog = await self.catalog(fresh=True)

        if ops is not None:
            try:
                spec = apply_ops(base.spec, ops)

            except (jsonpatch.JsonPatchException, jsonpointer.JsonPointerException) as e:
                raise ApiError(status_code=422, message=f"JSON Patch не применяется: {e}") from e

        try:
            checked = validate_spec(spec, catalog)

        except SpecError as e:
            raise ApiError(status_code=422, message="Задача не прошла проверку", details={"errors": e.errors}) from e

        return self._new_version(task_id, base, checked, catalog, patch=ops, summary=summary, created_by="manual", session_id=None)


    async def refresh_tools(self, task_id: str) -> dict[str, Any]:
        """A draft with the same steps and the tools as they are now: the user reads what
        changed in them and approves it, or not. Steps that no longer fit a tool's new
        arguments are named: the task is then changed in words, like any other."""
        base = store.base_version(task_id)
        # Fresh: a command approved a moment ago is what the user means.
        catalog = await self.catalog(fresh=True)

        tools = TaskSpec.model_validate(base.spec).tools()
        missing = down(tools, catalog)
        if missing:
            raise ApiError(status_code=409, message=f"Не запущены плагины команд {', '.join(sorted(missing))}: обновлять пока нечем")

        gone = sorted(set(tools) - set(catalog))
        if gone:
            # Not a change to approve: the steps have to go, the command come back.
            raise ApiError(
                status_code=409,
                message=f"Команд {', '.join(gone)} больше нет: обновить нечем — уберите их шаги (словами в чате задачи) или верните команды",
            )

        try:
            spec = validate_spec(base.spec, catalog)

        except SpecError as e:
            raise ApiError(
                status_code=422,
                message="Шаги задачи не подходят к новым определениям команд: поправьте задачу словами",
                details={"errors": e.errors},
            ) from e

        changed = differences(base.tools, snapshot(spec.tools(), catalog))
        if not changed and base.status == "draft":
            # Refreshed before: what is to approve is there already.
            raise ApiError(status_code=409, message=f"Черновик с командами, как они сейчас, уже ждёт одобрения: версия {base.id}")

        if not changed:
            raise ApiError(status_code=422, message="Команды задачи не менялись")

        summary = "Команды изменились: " + ", ".join(item["tool"] for item in changed)

        return self._new_version(task_id, base, spec, catalog, patch=None, summary=summary, created_by="tools", session_id=None)


    def _new_version(
        self,
        task_id: str,
        base: TaskVersion,
        spec: TaskSpec,
        catalog: dict[str, dict[str, Any]],
        *,
        patch: list[dict[str, Any]] | None,
        summary: str | None,
        created_by: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        cards = build_cards(spec, catalog)
        # Every version keeps the tools as they are now: a change since the base is shown.
        tools = snapshot(spec.tools(), catalog)
        if not diff_specs(TaskSpec.model_validate(base.spec), spec, catalog, differences(base.tools, tools))["changes"]:
            raise ApiError(status_code=422, message="Изменение ничего не меняет в задаче")

        # What the user approves is this against what runs now, not against a draft in
        # between: a change built on an earlier one shows both.
        active = store.active_version(task_id) or base
        diff = diff_specs(TaskSpec.model_validate(active.spec), spec, catalog, differences(active.tools, tools))

        version = store.add_version(
            task_id,
            spec,
            parent=base,
            patch=patch,
            cards=cards,
            summary=summary or summarize(spec),
            created_by=created_by,
            session_id=session_id,
            tools=tools,
        )

        return {
            "task_id": task_id,
            "version_id": version.id,
            "title": spec.title,
            "summary": version.summary,
            "description": spec.description,
            "trigger": describe_trigger(spec.trigger),
            "diff": diff["lines"],
            "changes": diff["changes"],
            "cards": cards,
        }


    async def diff(self, task_id: str, version_id: str) -> dict[str, Any]:
        version = store.get_version(task_id, version_id)
        if not version.parent_version_id:
            return {"changes": [], "lines": ["Первая версия задачи"]}

        # A draft against what runs now (what approving it changes); another against the
        # version it was made from.
        active = store.active_version(task_id) if version.status == "draft" else None
        parent = active if active is not None and active.id != version.id else store.get_version(task_id, version.parent_version_id)

        return diff_specs(
            TaskSpec.model_validate(parent.spec),
            TaskSpec.model_validate(version.spec),
            await self.catalog(),
            differences(parent.tools, version.tools),
        )


    async def catalog(self, *, fresh: bool = False) -> dict[str, dict[str, Any]]:
        return await self._catalog.get(fresh=fresh)


def apply_ops(spec: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    """JSON Patch where a step may be named by its id: /steps/totals/args/rows is
    /steps/5/args/rows — a model counting steps gets the number wrong, an id it copies."""
    # One at a time: an op that adds a step moves the numbers of the ones after it.
    for op in ops:
        spec = jsonpatch.apply_patch(spec, [{**op, **{key: _by_index(spec, op[key]) for key in ("path", "from") if isinstance(op.get(key), str)}}])

    return spec


def _by_index(spec: dict[str, Any], path: str) -> str:
    parts, node = path.split("/"), spec
    for i, part in enumerate(parts[1:], start=1):
        if isinstance(node, list) and parts[i - 1] == "steps" and not part.isdigit():
            ids = [step.get("id") for step in node if isinstance(step, dict)]
            if part in ids:
                parts[i] = str(ids.index(part))

        try:
            node = node[int(parts[i])] if isinstance(node, list) else node[parts[i].replace("~1", "/").replace("~0", "~")]
        except (KeyError, IndexError, ValueError, TypeError):
            break

    return "/".join(parts)


def step_paths(spec: dict[str, Any], prefix: str = "/steps") -> list[str]:
    """Where each step is: «/steps/5 (totals)», nested ones under their foreach."""
    paths = []
    for i, step in enumerate(spec.get("steps") or []):
        paths.append(f"{prefix}/{i} ({step.get('id')})")
        paths += step_paths(step, f"{prefix}/{i}/steps")

    return paths


def check_reflects_chat(spec: TaskSpec, chat_tools: set[str]) -> None:
    """A compiled task must redo at least one thing the chat actually did: a spec that
    shares no tool with the chat means the model lost the conversation."""
    used = {step.tool for step in spec.all_steps() if step.tool}

    if chat_tools and not used & chat_tools:
        raise SpecError([
            "задача не повторяет ни одного действия из разговора; "
            f"в разговоре успешно выполнялись: {sorted(chat_tools)}"
        ])


# Shorter strings (names, folders, short subjects) are too close to each other by chance.
_VERBATIM_MIN_LEN = 15
# A literal sharing this much with a chat text but not equal to any part of it is a retyped copy.
_NEAR_COPY = 0.9


def check_verbatim(spec: TaskSpec, texts: list[str]) -> None:
    """Texts the user gave must be copied, not retyped: a near-copy with a slip (a lost
    letter, a changed word) would go out in every run."""
    errors: list[str] = []
    values = [*spec.variables.values(), *(s for step in spec.all_steps() for s in strings_in(step.args))]

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
