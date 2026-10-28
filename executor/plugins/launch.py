"""How a plugin's server starts, and the executor's plugins while it runs.

A plugin of functions starts as our shim in the plugin's .venv; a plugin with a server
of its own starts its command — `python` (the plugin's) or a program of its .venv,
nothing from elsewhere on the machine. Either way: in the plugin's folder, with the
safe environment and the plugin's `env`, after its files were checked against the hash
they were approved (or saved) with. Its home is a folder of its own
(DATA_DIR/plugins/<name>/home): what a server keeps for itself (a cache, its own
settings) stays with the plugin, out of the user's home and out of the approved code.

A plugin with accounts runs a server per account (Upstream.for_account); the one that
gives the catalog its tools has the first account of the kind, or none yet. Its own
account kind (declared in the manifest) is offered for new accounts while the plugin
is approved."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

from mcp import StdioServerParameters

from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import Account, Plugin
from common.tools import Credential, ToolContext, ToolError
from common.tools.accounts import AccountField, AccountKind, account_kind, drop_account_kind
from executor.plugins import deps, store
from executor.plugins.folder import files_hash
from executor.plugins.manifest import PluginManifest, account_env
from executor.registry import ToolRegistry
from executor.upstream import Upstream, account_key

# The first account of a kind (by name), or None: whose server gives the catalog its tools.
FirstAccount = Callable[[str], Credential | None]


log = logging.getLogger("plugins")

SHIM = Path(__file__).with_name("shim.py")

# Besides the SDK's safe few (PATH, HOME…): so the interpreter and the OS work as usual.
_KEEP_ENV = ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR", "COMSPEC")
# Where a program keeps its own files, on Linux, macOS and Windows: the plugin's home.
_HOME_ENV = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")


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

    home = storage.plugins_dir / row.name / "home"
    home.mkdir(parents=True, exist_ok=True)
    homes = {key: str(home) for key in _HOME_ENV}

    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ} | homes | manifest.env

    return StdioServerParameters(command=command, args=args, env=env, cwd=folder)


def upstream_of(storage: StorageSettings, row: Plugin, first_account: FirstAccount | None = None) -> Upstream:
    manifest = PluginManifest.model_validate(row.manifest)
    params = server_params(storage, row)
    credential = None

    if manifest.account is not None:
        credential = first_account(manifest.account.kind) if first_account else None
        params = params.model_copy(update={"env": {**params.env, **account_env(manifest.account, credential)}})

    return Upstream(row.name, params, manifest, code_hash=row.hash, account_key=account_key(manifest, credential))


def first_account(load: Callable[[str], Credential]) -> FirstAccount:
    """FirstAccount over the accounts table and a loader of their secrets."""
    def find(kind: str) -> Credential | None:
        with session() as db:
            row = db.query(Account).filter(Account.kind == kind).order_by(Account.name).first()

        try:
            return load(row.name) if row else None

        except ToolError:
            return None   # no password yet: as if there were no account

    return find


def kind_of(plugin: str, manifest: PluginManifest) -> AccountKind | None:
    """The plugin's own account kind, if its manifest declares one (`account.fields`)."""
    spec = manifest.account
    if spec is None or spec.fields is None:
        return None

    required = [field.name for field in spec.fields if field.required]

    def prepare(config: dict[str, Any]) -> dict[str, Any]:
        missing = [name for name in required if not config.get(name)]
        if missing:
            raise ValueError(f"не хватает настроек: {', '.join(missing)}")

        return config

    return AccountKind(
        name=spec.kind,
        title=spec.title or manifest.title,
        fields=tuple(
            AccountField(f.name, f.title, type=f.type, required=f.required, choices=tuple(f.choices), hint=f.hint, default=f.default)
            for f in spec.fields
        ),
        secret_title=spec.secret_title,
        prepare=prepare,
        owner=plugin,
    )


class Plugins:
    """The approved plugins' servers in the registry; what went wrong with each."""

    def __init__(self, registry: ToolRegistry, storage: StorageSettings, first_account: FirstAccount | None = None):

        self.registry      = registry
        self.storage       = storage
        self.first_account = first_account
        # What went wrong with each, the registry's: the catalog tells whoever asks.
        self.errors = registry.unavailable
        # The account kind each plugin declared, by plugin.
        self._kinds: dict[str, str] = {}


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

        # Its account kind first: accounts of it can be made even before its server runs.
        self._offer_kind(name, PluginManifest.model_validate(row.manifest))

        try:
            upstream = await asyncio.to_thread(upstream_of, self.storage, row, self.first_account)
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

        if name in self._kinds:
            drop_account_kind(self._kinds.pop(name), name)


    async def account_changed(self, kind: str) -> None:
        """An account of this kind was made, changed or removed: the plugins that use the
        kind start again (their catalog server takes the first account; the account
        servers start anew at their next call)."""
        for row in store.rows("current"):
            spec = PluginManifest.model_validate(row.manifest).account
            if spec is not None and spec.kind == kind:
                await self.start(row.name)


    def _offer_kind(self, name: str, manifest: PluginManifest) -> None:
        kind = kind_of(name, manifest)

        if name in self._kinds and (kind is None or self._kinds[name] != kind.name):
            drop_account_kind(self._kinds.pop(name), name)

        if kind is not None:
            try:
                account_kind(kind)
                self._kinds[name] = kind.name

            except ValueError:
                log.error("plugin %s: account kind %s is another's", name, kind.name)


    def state(self, name: str) -> dict[str, Any]:
        upstream = self.registry.upstreams.get(name)

        return {"running": upstream is not None, "error": self.errors.get(name), "tools": sorted(spec for spec in upstream.specs()) if upstream else []}


async def check_draft(storage: StorageSettings, name: str, first_account: FirstAccount | None = None) -> dict[str, Any]:
    """Run the draft's server once to see the tools it offers: what the person reads
    before approving. The person asked for it: the code is not approved yet."""
    row = store.row(name, "draft")
    if row is None:
        raise ToolError(f"У плагина «{name}» нет черновика")

    upstream = await asyncio.to_thread(upstream_of, storage, row, first_account)

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

    if PluginManifest.model_validate(row.manifest).account is not None:
        raise ToolError(f"Плагин «{name}» работает от имени аккаунта: его пробуют после одобрения")

    upstream = await asyncio.to_thread(upstream_of, storage, row)

    try:
        await upstream.start()
        spec = upstream.specs().get(f"{name}.{tool}")
        if spec is None:
            raise ToolError(f"В черновике «{name}» нет инструмента {tool}")

        return await spec.call(spec.parse(args), ctx)

    finally:
        await upstream.stop()
