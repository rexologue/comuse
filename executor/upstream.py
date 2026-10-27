"""Other MCP servers whose tools the executor offers as its own: the aggregator.

An upstream is a server the executor starts itself and talks to over stdio (a plugin
brings its code, executor/plugins later says how to start it). Its tools join the
catalog as `<group>.<tool>`, next to the built-in ones and the own commands, with the
same face (UpstreamToolSpec: parse, call, manifest): the chat, the runner and the
compiler see no difference, and a task keeps their definitions' hashes like any other
(a server that changes a tool stops the tasks that call it).

What the server gets: its arguments, and an environment of the few variables the SDK
deems safe (PATH, HOME…) plus what the upstream is given — none of the executor's
tokens or keys. What it can do besides is what its code does: like an own command, it
is safe because a person read and approved it, not because of a sandbox.

The session lives as long as the executor, in a task of its own (the SDK's transport
runs in a task group, which must be left in the task that entered it). A server that
dies is started again on the next call; a server that changes its tools says so
(notifications/tools/list_changed) and the catalog is made anew."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import anyio
import jsonschema
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError

from common.tools import Confirm, ToolContext, ToolError, function_name
from common.tools.definition import definition_hash


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
        *,
        confirm: Confirm = True,
        on_change: Callable[[], None] | None = None,
    ):

        self.group     = group
        self.params    = params
        # Whether the chat asks before a call. A server's own hints (readOnlyHint) are
        # its word only: the person who approves the plugin decides.
        self.confirm   = confirm
        self.on_change = on_change

        self.tools: list[types.Tool] = []
        self._session: ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event = asyncio.Event()
        self._stop: asyncio.Event = asyncio.Event()
        self._failure: BaseException | None = None
        self._starting = asyncio.Lock()


    async def start(self) -> None:
        """Start the server (if it is not running) and wait until it has listed its tools."""
        async with self._starting:
            if self._task is not None and not self._task.done():
                return

            self._ready, self._stop, self._failure = asyncio.Event(), asyncio.Event(), None
            self._task = asyncio.create_task(self._serve(), name=f"upstream:{self.group}")

            try:
                await asyncio.wait_for(self._ready.wait(), _START_TIMEOUT_SEC)

            except TimeoutError as e:
                await self.stop()
                raise ToolError(f"Сервер «{self.group}» не ответил за {_START_TIMEOUT_SEC} с") from e

            if self._failure is not None:
                raise ToolError(f"Сервер «{self.group}» не запустился: {self._failure!r}") from self._failure


    async def stop(self) -> None:
        self._stop.set()

        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 10)

            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()

            self._task = None

        self._session = None


    async def ensure_running(self) -> None:
        if self._session is None or self._task is None or self._task.done():
            log.info("upstream %s is not running: starting it", self.group)
            await self.start()


    async def call(self, name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        """One call. A server that went away is not called again here (whether the call
        reached it is unknown): it is stopped, and the next call starts it anew."""
        await self.ensure_running()

        try:
            return await self._session.call_tool(name, arguments)

        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as e:
            await self.stop()
            raise ToolError(f"Сервер «{self.group}» закрыл соединение") from e

        except McpError as e:
            if e.error.code == types.CONNECTION_CLOSED:
                await self.stop()
                raise ToolError(f"Сервер «{self.group}» закрыл соединение") from e

            raise ToolError(f"Сервер «{self.group}»: {e.error.message}") from e


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
        """Its tools as `<group>.<tool>`; a tool whose name clashes with another is left out."""
        found: dict[str, UpstreamToolSpec] = {}

        for tool in self.tools:
            name = f"{self.group}.{_local_name(tool.name)}"
            if name in found:
                log.error("upstream %s: tool %s clashes with another one, left out", self.group, tool.name)
                continue

            found[name] = UpstreamToolSpec.of(self, tool, name)

        return found


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
    # Third-party servers do not see the workspace: no path arguments, no accounts here.
    path_args: dict[str, str] = field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    core: bool = False
    _validator: Any = field(default=None, compare=False)

    @classmethod
    def of(cls, upstream: Upstream, tool: types.Tool, name: str) -> UpstreamToolSpec:
        title = tool.title or (tool.annotations.title if tool.annotations and tool.annotations.title else None) or tool.name

        return cls(
            name=name,
            title=title,
            card=title,
            description=tool.description or title,
            confirm=upstream.confirm,
            timeout=None,
            input_schema=tool.inputSchema,
            output_schema=tool.outputSchema or TEXT_OUTPUT,
            upstream=upstream,
            remote_name=tool.name,
            _validator=jsonschema.Draft202012Validator(tool.inputSchema),
        )


    def manifest(self) -> dict[str, Any]:
        manifest = {
            "name": self.name,
            "function_name": function_name(self.name),
            "title": self.title,
            "card": self.card,
            "description": self.description,
            "confirm": self.confirm,
            "secrets": [],
            "timeout": self.timeout,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "settings_schema": None,
            "guide": None,
            "core": False,
            "chat_only": False,
            "source": "plugin",
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
        result = await self.upstream.call(self.remote_name, inp)
        text = "\n".join(item.text for item in result.content if isinstance(item, types.TextContent))

        if result.isError:
            raise ToolError(text or f"`{self.name}` вернул ошибку без текста")

        if self.output_schema is TEXT_OUTPUT:
            return {"text": text}

        if result.structuredContent is None:
            raise ToolError(f"`{self.name}` не вернул данные по своей схеме")

        return result.structuredContent
