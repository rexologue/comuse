"""How a plugin's server starts, and the executor's plugins while it runs.

A plugin of functions starts as our shim in the plugin's .venv; a plugin with a server
of its own starts its command — `python` (the plugin's) or a program of its .venv,
nothing from elsewhere on the machine. Either way: in the plugin's folder, with the
safe environment and the plugin's `env`, after its files were checked against the hash
they were approved (or saved) with."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters

from common.settings import StorageSettings
from common.storage.models import Plugin
from common.tools import ToolContext, ToolError
from executor.plugins import deps, store
from executor.plugins.folder import files_hash
from executor.plugins.manifest import PluginManifest
from executor.registry import ToolRegistry
from executor.upstream import Upstream


log = logging.getLogger("plugins")

SHIM = Path(__file__).with_name("shim.py")

# Besides the SDK's safe few (PATH, HOME…): so the interpreter and the OS work as usual.
_KEEP_ENV = ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "COMSPEC")


def server_params(storage: StorageSettings, row: Plugin) -> StdioServerParameters:
    """The server of the plugin's current code or draft (`row.status`). Builds its .venv
    on the first start (a blocking call: run it in a thread)."""
    folder = store.plugin_dir(storage, row.name, row.status)

    if files_hash(folder) != row.hash:
        raise ToolError(f"Файлы плагина «{row.name}» изменились после {'одобрения' if row.status == 'current' else 'сохранения'}: сервер не запущен")

    manifest = PluginManifest.model_validate(row.manifest)
    python = deps.ensure_venv(folder, storage.uv_cache_dir)

    if manifest.tools is not None:
        # -I ignores PYTHON* variables and the user's site, -B writes no __pycache__ into
        # the frozen folder, -X utf8 whatever the OS locale.
        command, args = str(python), ["-I", "-B", "-X", "utf8", str(SHIM), str(folder)]

    elif manifest.command[0] == "python":
        command, args = str(python), manifest.command[1:]

    else:
        program = python.parent / manifest.command[0]
        found = next((path for path in (program, program.with_suffix(".exe")) if path.is_file()), None)
        if found is None:
            raise ToolError(f"Плагин «{row.name}»: в его .venv нет программы {manifest.command[0]}")

        command, args = str(found), manifest.command[1:]

    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ} | manifest.env

    return StdioServerParameters(command=command, args=args, env=env, cwd=folder)


def upstream_of(storage: StorageSettings, row: Plugin) -> Upstream:
    return Upstream(row.name, server_params(storage, row), PluginManifest.model_validate(row.manifest), code_hash=row.hash)


class Plugins:
    """The approved plugins' servers in the registry; what went wrong with each."""

    def __init__(self, registry: ToolRegistry, storage: StorageSettings):

        self.registry = registry
        self.storage  = storage
        self.errors: dict[str, str] = {}


    async def start_all(self) -> None:
        # Drafts a migration made have their packages resolved first.
        for row in store.rows("draft"):
            if row.packages is None:
                try:
                    await asyncio.to_thread(store.finish_draft, self.storage, row.name)

                except ToolError as e:
                    log.error("plugin %s: draft not finished: %s", row.name, e)

        for row in store.rows("current"):
            await self.start(row.name)


    async def start(self, name: str) -> None:
        """Start (or restart) the plugin's approved code; a failure is kept, not raised."""
        row = store.row(name, "current")
        if row is None:
            await self.stop(name)
            return

        try:
            upstream = await asyncio.to_thread(upstream_of, self.storage, row)
            await self.registry.connect(upstream)
            self.errors.pop(name, None)

        except ToolError as e:
            # Not the code that was approved, or a server that does not start: none runs.
            log.error("plugin %s not started: %s", name, e)
            await self.registry.disconnect(name)
            self.errors[name] = str(e)


    async def stop(self, name: str) -> None:
        self.errors.pop(name, None)
        await self.registry.disconnect(name)


    def state(self, name: str) -> dict[str, Any]:
        upstream = self.registry.upstreams.get(name)

        return {"running": upstream is not None, "error": self.errors.get(name), "tools": sorted(spec for spec in upstream.specs()) if upstream else []}


async def check_draft(storage: StorageSettings, name: str) -> dict[str, Any]:
    """Run the draft's server once to see the tools it offers: what the person reads
    before approving. The person asked for it: the code is not approved yet."""
    row = store.row(name, "draft")
    if row is None:
        raise ToolError(f"У плагина «{name}» нет черновика")

    upstream = await asyncio.to_thread(upstream_of, storage, row)

    try:
        await upstream.start()
        tools = [spec.manifest() for spec in upstream.specs().values()]

    finally:
        await upstream.stop()

    shown = [{key: tool[key] for key in ("name", "title", "description", "confirm", "input_schema", "output_schema")} for tool in tools]
    await asyncio.to_thread(store.set_tools, name, row.hash, shown)

    return await asyncio.to_thread(store.view, storage, name, "draft")


async def try_draft(storage: StorageSettings, name: str, tool: str, args: dict[str, Any], ctx: ToolContext) -> Any:
    """One call of a draft's tool (tools.try in a chat): its server for this call only."""
    row = store.row(name, "draft")
    if row is None:
        raise ToolError(f"У плагина «{name}» нет черновика: сначала tools.draft")

    upstream = await asyncio.to_thread(upstream_of, storage, row)

    try:
        await upstream.start()
        spec = upstream.specs().get(f"{name}.{tool}")
        if spec is None:
            raise ToolError(f"В черновике «{name}» нет инструмента {tool}")

        return await spec.call(spec.parse(args), ctx)

    finally:
        await upstream.stop()
