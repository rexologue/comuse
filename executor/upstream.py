"""MCP servers whose tools the executor offers as its own: the aggregator.

An upstream is a plugin's server (executor/plugins) the executor starts itself and talks
to over stdio. Its tools join the catalog as `<group>.<tool>`, next to the built-in ones,
with the same face (UpstreamToolSpec: parse, call, manifest): the chat, the runner and
the compiler see no difference, and a task keeps their definitions' hashes like any
other (a server that changes a tool, or new code of the plugin, stops the tasks that
call it).

What the server gets: its arguments, and an environment of the few variables the SDK
deems safe (PATH, HOME…) plus what the plugin is given — none of the executor's tokens
or keys. What it can do besides is what its code does: it is safe because a person
read and approved it, not because of a sandbox.

What MCP has no field for travels in `_meta` both ways (common/tools/mcp_codec.py): the
call's context (mode, dry_run, output_dir, run_id…) goes to the server, its logs,
notifications and the files it made come back. A server that does not know them
ignores them: then a dry run does not call its tools that change anything.

The session lives as long as the executor, in a task of its own (the SDK's transport
runs in a task group, which must be left in the task that entered it). A server that
dies is started again on the next call; a server that changes its tools says so
(notifications/tools/list_changed) and the catalog is made anew."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import anyio
import jsonschema
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from common.tools import Confirm, Credential, ToolContext, ToolError, function_name
from common.tools.context import PermissionDenied
from common.tools.definition import definition_hash
from common.tools.fields import PATH_ARG
from executor.plugins.manifest import PluginManifest, account_env, path_args


log = logging.getLogger("upstream")

# How long a server may take to start and list its tools.
_START_TIMEOUT_SEC = 60

# A tool without an output schema answers in text: {"text": …} is its output.
TEXT_OUTPUT = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}


class Upstream:
    def __init__(
        self,
        group: str,
        params: StdioServerParameters,
        manifest: PluginManifest,
        *,
        code_hash: str | None = None,
        account_key: str | None = None,
        on_change: Callable[[], None] | None = None,
    ):

        self.group     = group
        self.params    = params
        # How its tools are shown and asked about: the plugin's word, which a person
        # approved — not the server's own hints (readOnlyHint).
        self.manifest  = manifest
        # The plugin's files: other code, other tools for the tasks that call them.
        self.code_hash = code_hash
        # Whose account's settings this process has in its environment (account_key()).
        self.account_key = account_key
        self.on_change = on_change
        # A plugin with accounts: a server per account, started at its first call.
        self._accounts: dict[str, Upstream] = {}

        self.tools: list[types.Tool] = []
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._stop: asyncio.Event = asyncio.Event()
        self._failure: BaseException | None = None
        self._starting = asyncio.Lock()


    async def start(self) -> None:
        """Start the server (if it is not running) and wait until it has listed its tools.
        One that is going away (a call given up on) is waited for first."""
        async with self._starting:
            if self._session is not None and self._task is not None and not self._task.done():
                return

            await self._end()

            self._ready, self._stop, self._failure = asyncio.Event(), asyncio.Event(), None
            self._task = asyncio.create_task(self._serve(), name=f"upstream:{self.group}")

            try:
                await asyncio.wait_for(self._ready.wait(), _START_TIMEOUT_SEC)

            except TimeoutError as e:
                await self._end()
                raise ToolError(f"Сервер «{self.group}» не ответил за {_START_TIMEOUT_SEC} с") from e

            if self._failure is not None:
                raise ToolError(f"Сервер «{self.group}» не запустился: {self._failure!r}") from self._failure


    async def stop(self) -> None:
        async with self._starting:
            await self._end()

        for child in self._accounts.values():
            await child.stop()

        self._accounts = {}


    def for_account(self, credential: Credential) -> Upstream:
        """The server of this account: this one if it has it, else one of its own — made
        anew when the account's settings or secret changed."""
        key = account_key(self.manifest, credential)
        if key == self.account_key:
            return self

        child = self._accounts.get(credential.name)
        if child is None or child.account_key != key:
            if child is not None:
                asyncio.create_task(child.stop())

            env = {**(self.params.env or {}), **account_env(self.manifest.account, credential)}
            child = Upstream(self.group, self.params.model_copy(update={"env": env}), self.manifest, code_hash=self.code_hash, account_key=key)
            self._accounts[credential.name] = child

        return child


    async def _end(self) -> None:
        self._stop.set()
        self._session = None
        task, self._task = self._task, None
        await _reap(task)


    async def ensure_running(self) -> None:
        if self._session is None or self._task is None or self._task.done():
            log.info("upstream %s is not running: starting it", self.group)
            await self.start()


    async def call(self, name: str, arguments: dict[str, Any], meta: dict[str, Any] | None = None) -> types.CallToolResult:
        """One call. A server that went away is not called again here (whether the call
        reached it is unknown): it is stopped, and the next call starts it anew. So is a
        server whose call was given up (a timeout): what it still does is not waited for."""
        await self.ensure_running()
        # This server, not whichever runs when the call ends: one started meanwhile stays.
        session, task, stop = self._session, self._task, self._stop

        try:
            return await session.call_tool(name, arguments, meta=meta)

        except asyncio.CancelledError:
            # No call goes to it any more; its process is stopped in the background.
            if self._task is task:
                self._session, self._task = None, None

            stop.set()
            asyncio.create_task(_reap(task))
            raise

        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as e:
            await self.stop()
            raise ToolError(f"Сервер «{self.group}» закрыл соединение") from e

        except McpError as e:
            if e.error.code == types.CONNECTION_CLOSED:
                await self.stop()
                raise ToolError(f"Сервер «{self.group}» закрыл соединение") from e

            raise ToolError(f"Сервер «{self.group}»: {e.error.message}") from e

        except RuntimeError as e:
            # The SDK checks a result against the tool's output schema.
            raise ToolError(f"Ответ сервера «{self.group}» не по схеме инструмента: {e}") from e


    async def _serve(self) -> None:
        try:
            with open(os.devnull, "w") as quiet:
                # Its stderr is not ours to keep: whatever the server prints may hold the
                # secrets it was given.
                async with stdio_client(self.params, errlog=quiet) as (read, write):
                    async with ClientSession(read, write, message_handler=self._on_message) as session:
                        await session.initialize()
                        self.tools = (await session.list_tools()).tools
                        self._session = session
                        self._ready.set()

                        log.info("upstream %s: %d tools", self.group, len(self.tools))
                        await self._stop.wait()

        except Exception as e:
            log.warning("upstream %s stopped: %r", self.group, e)
            self._failure = e

        finally:
            self._session = None
            self._ready.set()


    async def _on_message(self, message: Any) -> None:
        if isinstance(message, types.ServerNotification) and isinstance(message.root, types.ToolListChangedNotification):
            asyncio.create_task(self._relist())


    async def _relist(self) -> None:
        if self._session is None:
            return

        self.tools = (await self._session.list_tools()).tools
        log.info("upstream %s changed its tools: %d now", self.group, len(self.tools))

        if self.on_change is not None:
            self.on_change()


    def specs(self) -> dict[str, UpstreamToolSpec]:
        """Its tools as `<group>.<tool>`; one the plugin hides, or whose name clashes with
        another, is left out."""
        found: dict[str, UpstreamToolSpec] = {}

        for tool in self.tools:
            if tool.name in self.manifest.hidden_tools:
                continue

            name = f"{self.group}.{_local_name(tool.name)}"
            if name in found:
                log.error("upstream %s: tool %s clashes with another one, left out", self.group, tool.name)
                continue

            found[name] = UpstreamToolSpec.of(self, tool, name)

        return found


def account_key(manifest: PluginManifest, credential: Credential | None) -> str | None:
    """Which account, with which settings, a server's environment holds (a hash: the
    secret is in it)."""
    if manifest.account is None or credential is None:
        return None

    env = json.dumps(account_env(manifest.account, credential), sort_keys=True)
    return f"{credential.name}:{hashlib.sha256(env.encode()).hexdigest()}"


async def _reap(task: asyncio.Task | None) -> None:
    """Wait for a server's task told to stop; one that does not, is cancelled."""
    if task is None or task.done():
        return

    try:
        await asyncio.wait_for(task, 10)

    except (TimeoutError, asyncio.CancelledError):
        task.cancel()


def _local_name(name: str) -> str:
    """A tool's name as the part after the group: what function names allow, no dots."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


@dataclass(slots=True, frozen=True)
class UpstreamToolSpec:
    name: str
    title: str
    card: str
    description: str
    confirm: Confirm
    timeout: int | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    upstream: Upstream = field(compare=False)
    # The tool's name on its server.
    remote_name: str = ""
    # Arguments marked as workspace paths ({"x-path": …}): checked, given as full paths.
    path_args: dict[str, str] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    core: bool = False
    _validator: Any = field(default=None, compare=False)

    @classmethod
    def of(cls, upstream: Upstream, tool: types.Tool, name: str) -> UpstreamToolSpec:
        manifest = upstream.manifest
        entry = manifest.entry_of(tool.name)
        title = tool.title or (tool.annotations.title if tool.annotations and tool.annotations.title else None) or tool.name
        schema = _with_paths(_with_account(tool.inputSchema, manifest), manifest.path_args.get(tool.name, {}))

        return cls(
            name=name,
            title=title,
            card=(entry.card if entry else "") or title,
            description=tool.description or title,
            confirm=entry.confirm if entry else manifest.confirm_by_tool.get(tool.name, manifest.confirm),
            timeout=(entry.timeout if entry else None) or manifest.timeout,
            input_schema=schema,
            output_schema=tool.outputSchema or TEXT_OUTPUT,
            upstream=upstream,
            remote_name=tool.name,
            path_args=path_args(schema),
            secrets=("account",) if manifest.account else (),
            _validator=jsonschema.Draft202012Validator(schema),
        )


    def manifest(self) -> dict[str, Any]:
        manifest = {
            "name": self.name,
            "function_name": function_name(self.name),
            "title": self.title,
            "card": self.card,
            "description": self.description,
            "confirm": self.confirm,
            "secrets": list(self.secrets),
            "timeout": self.timeout,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "settings_schema": None,
            "guide": None,
            "core": False,
            "chat_only": False,
            "source": "plugin",
            "plugin": self.upstream.group,
            "code_hash": self.upstream.code_hash,
        }

        return {**manifest, "hash": definition_hash(manifest)}


    def parse(self, args: dict[str, Any]) -> dict[str, Any]:
        errors = sorted(self._validator.iter_errors(args), key=lambda e: list(e.path))
        if errors:
            raise ToolError("; ".join(f"{'.'.join(map(str, e.path)) or 'аргументы'}: {e.message}" for e in errors[:5]))

        return dict(args)


    async def prepare(self) -> None:
        """A server that went away is started again before the call's timeout starts."""
        await self.upstream.ensure_running()


    async def call(self, inp: dict[str, Any], ctx: ToolContext) -> Any:
        # A dry run changes nothing: a plugin that cannot do one is not called for a tool
        # that would change something; the log says what would have been called.
        if ctx.dry_run and self.confirm is not False and not self.upstream.manifest.handles_dry_run():
            ctx.log(f"Пробный запуск: `{self.name}` не вызван (плагин не умеет пробный запуск), вызвал бы с {inp}")
            return {}

        context = {
            "mode": ctx.mode,
            "dry_run": ctx.dry_run,
            "output_dir": str(ctx.output_dir()),
            "session_id": ctx.session_id,
            "run_id": ctx.run_id,
            "step_key": ctx.step_key,
        }
        args = {key: self._full_paths(key, value, ctx) for key, value in inp.items()}
        server = self.upstream

        # The account the model named is a process of its own; the server's own argument
        # for it gets the account's name.
        spec = self.upstream.manifest.account
        if spec is not None:
            credential = ctx.credential("account")
            if credential.kind != spec.kind:
                raise ToolError(f"Аккаунт `{credential.name}` не подходит `{self.name}`: нужен вид {spec.kind}, а он {credential.kind}")

            args.pop("account", None)
            if spec.argument:
                args[spec.argument] = credential.name

            server = self.upstream.for_account(credential)

        result = await server.call(self.remote_name, args, {"context": context})
        said = result.meta or {}

        for line in said.get("logs") or []:
            ctx.log(str(line))

        for message in said.get("notifications") or []:
            ctx.notify(str(message))

        text = "\n".join(item.text for item in result.content if isinstance(item, types.TextContent))

        if result.isError:
            if said.get("traceback"):
                ctx.log(said["traceback"])

            raise ToolError(text or f"`{self.name}` вернул ошибку без текста")

        for path in said.get("artifacts") or []:
            ctx.artifacts.append(_ref(Path(path), ctx) or path)

        if self.output_schema is TEXT_OUTPUT:
            return {"text": text}

        if result.structuredContent is None:
            raise ToolError(f"`{self.name}` не вернул данные по своей схеме")

        return _refs_back(result.structuredContent, ctx)


    def _full_paths(self, key: str, value: Any, ctx: ToolContext) -> Any:
        """Workspace refs become full paths: the plugin works with files, not our refs."""
        kind = self.path_args.get(key)
        if kind == "path" and isinstance(value, str):
            return str(ctx.read_path(value))

        if kind == "refs" and isinstance(value, list):
            return [str(ctx.read_path(item)) if isinstance(item, str) else item for item in value]

        if kind == "refs" and isinstance(value, str):
            return str(ctx.read_path(value))

        return value


def _with_account(schema: dict[str, Any], manifest: PluginManifest) -> dict[str, Any]:
    """A tool of a plugin with accounts, as the model sees it: our `account` argument in
    place of the server's own (which the executor fills in)."""
    spec = manifest.account
    if spec is None:
        return schema

    schema = copy.deepcopy(schema)
    properties = schema.setdefault("properties", {})
    if spec.argument:
        properties.pop(spec.argument, None)

    properties["account"] = {"type": "string", "description": f"Имя аккаунта из настроек (вид {spec.kind})"}
    schema["required"] = ["account", *(name for name in schema.get("required", []) if name not in (spec.argument, "account"))]

    return schema


def _with_paths(schema: dict[str, Any], marks: dict[str, str]) -> dict[str, Any]:
    """The arguments the plugin's manifest names as workspace paths, marked as ours are
    ({"x-path": …}): the model sees them as paths in the workspace, the executor checks them."""
    if not marks:
        return schema

    schema = copy.deepcopy(schema)

    for name, kind in marks.items():
        prop = (schema.get("properties") or {}).get(name)
        if not isinstance(prop, dict):
            continue

        target = prop["items"] if kind == "refs" and isinstance(prop.get("items"), dict) else prop
        target[PATH_ARG] = "path" if target is not prop else kind
        prop["description"] = (prop.get("description", "") + " Пути — в рабочей папке, как их дают другие команды.").strip()

    return schema


def _refs_back(value: Any, ctx: ToolContext) -> Any:
    """Full paths in the answer back to refs (`Отчёты/x.xlsx`, `data:outputs/…`)."""
    if isinstance(value, dict):
        return {key: _refs_back(item, ctx) for key, item in value.items()}

    if isinstance(value, list):
        return [_refs_back(item, ctx) for item in value]

    if isinstance(value, str) and len(value) < 1000 and Path(value).is_absolute():
        return _ref(Path(value), ctx) or value

    return value


def _ref(path: Path, ctx: ToolContext) -> str | None:
    try:
        return ctx.to_ref(path)

    except (PermissionDenied, ValueError, OSError):
        return None
