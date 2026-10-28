"""Plugins: their folders and rows. Only the executor calls this.

A plugin has its approved code (it runs) and at most one draft, changed in place until
a person approves it. The person approves what they read: the code, the exact packages
and the tools it offers — from the manifest for functions, from the server itself (a
check run, launch.py) for a server of its own; the approval names the draft's hash, and
a draft rewritten since is not approved. The approved code never changes: its files
must keep matching the hash, or the server does not start. Writing, throwing away and
approving one plugin's draft go one at a time."""

from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Collection

from pydantic import ValidationError

from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import Plugin
from common.tools import ToolError
from common.tools.accounts import get_account_kind
from common.utils import new_id, utcnow
from executor.plugins import deps, folder as code
from executor.plugins.folder import Which, files_hash
from executor.plugins.manifest import NAME_PATTERN, PluginManifest


MANIFEST = "plugin.json"

_RESERVED = {MANIFEST, deps.REQUIREMENTS, deps.LOCK}

_LOCKS: dict[str, threading.Lock] = {}


class NotFound(ToolError):
    """No such plugin, or no draft (or approved code) of it."""
_LOCKS_GUARD = threading.Lock()


def plugin_dir(storage: StorageSettings, name: str, which: Which) -> Path:
    return storage.plugins_dir / name / which


def save_draft(
    storage: StorageSettings,
    name: str,
    *,
    manifest: dict[str, Any],
    files: dict[str, str],
    requirements: list[str],
    created_by: str = "user",
    session_id: str | None = None,
    only_own: bool = False,
    taken_groups: Collection[str] = (),
) -> dict[str, Any]:
    """Write the plugin's draft (a new one, or over the draft there already is).
    Everything wrong is named at once, before anything is written. `only_own`: a draft
    someone else wrote (a person, another chat) is not replaced. `taken_groups`: groups
    of other tools (the built-in ones), which a plugin cannot take."""
    checked = _check(name, manifest, files, requirements, taken_groups)

    with _lock(name):
        with session() as db:
            draft = _row(db, name, "draft")

        if only_own and draft and (draft.created_by, draft.session_id) != (created_by, session_id):
            who = "другой чат" if draft.created_by == "chat" else "пользователь на странице «Плагины»"
            raise ToolError(
                f"У плагина «{name}» уже есть черновик: его написал {who}. "
                "Он не заменён: пусть пользователь одобрит или выбросит его, или выбери другое имя"
            )

        folder = plugin_dir(storage, name, "draft")
        # Packages first, aside: when they do not resolve, the draft stays as it was —
        # and a plugin that was not there before leaves no empty folder behind.
        try:
            lock_text, packages = code.resolve(folder, requirements, storage.uv_cache_dir)

        except ToolError:
            with session() as db:
                known = db.query(Plugin).filter_by(name=name).count()
            if not known:
                shutil.rmtree(storage.plugins_dir / name, ignore_errors=True)
            raise
        code.write(
            folder,
            files,
            manifest_name=MANIFEST,
            manifest_text=json.dumps(checked.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
            requirements=requirements,
            lock_text=lock_text,
        )

        with session() as db:
            row = _row(db, name, "draft") or Plugin(id=new_id("plg_"), name=name, status="draft")
            _fill(row, folder, checked, packages)
            row.created_by = created_by
            row.session_id = session_id
            db.add(row)
            db.commit()

    return view(storage, name, "draft")


def save_tool(
    storage: StorageSettings,
    group: str,
    action: str,
    *,
    entry: dict[str, Any],
    files: dict[str, str],
    requirements: list[str],
    created_by: str = "user",
    session_id: str | None = None,
    only_own: bool = False,
    taken_groups: Collection[str] = (),
) -> dict[str, Any]:
    """One function `<group>.<action>` into the draft of plugin `<group>` (made from its
    approved code if it has no draft, or new): its files go to `<action>/`, its entry
    is `<action>/main.py`, its packages join the plugin's. What a chat writes: a command
    at a time, the plugin is their group."""
    with session() as db:
        base = _row(db, group, "draft") or _row(db, group, "current")

    manifest = dict(base.manifest) if base else {"title": group, "description": "", "tools": []}
    if base is not None and manifest.get("tools") is None:
        raise ToolError(f"Плагин «{group}» — сервер со своей командой запуска: функции в него не добавить, выбери другую группу")

    kept, kept_requirements = code.read(plugin_dir(storage, group, base.status), _RESERVED) if base else ({}, [])
    prefix = f"{action}/"

    manifest["tools"] = [tool for tool in manifest["tools"] if tool["name"] != action] + [{**entry, "name": action, "entry": f"{prefix}main.py"}]
    # tools.show gives a command's files as `<action>/main.py`: written back the same way,
    # they are the same files, not `<action>/<action>/main.py`.
    own = {path.removeprefix(prefix): text for path, text in files.items()}
    merged = {path: text for path, text in kept.items() if not path.startswith(prefix)} | {prefix + path: text for path, text in own.items()}
    packages = list(dict.fromkeys([*kept_requirements, *requirements]))

    return save_draft(
        storage, group, manifest=manifest, files=merged, requirements=packages,
        created_by=created_by, session_id=session_id, only_own=only_own, taken_groups=taken_groups,
    )


def finish_draft(storage: StorageSettings, name: str) -> None:
    """A draft whose packages are not resolved yet (a migration made it): resolve them
    from its requirements.txt and take its hash and tools, as a save would."""
    with _lock(name):
        folder = plugin_dir(storage, name, "draft")
        requirements = code.read(folder, _RESERVED)[1]
        lock_text, packages = code.resolve(folder, requirements, storage.uv_cache_dir)
        (folder / deps.LOCK).write_text(lock_text, encoding="utf-8")

        with session() as db:
            row = _row(db, name, "draft")
            _fill(row, folder, PluginManifest.model_validate(row.manifest), packages)
            db.commit()


def _fill(row: Plugin, folder: Path, manifest: PluginManifest, packages: list[str]) -> None:
    row.hash = files_hash(folder)
    row.manifest = manifest.model_dump(exclude_none=True)
    row.packages = packages

    # Functions show their tools in the manifest; a server of its own shows them when it runs.
    if manifest.tools is not None:
        row.tools = [_tool_of_entry(row.name, entry) for entry in manifest.tools]
        row.tools_hash = row.hash
    else:
        row.tools, row.tools_hash = None, None


def _tool_of_entry(group: str, entry) -> dict[str, Any]:
    return {
        "name": f"{group}.{entry.name}",
        "title": entry.title,
        "description": entry.description,
        "confirm": entry.confirm,
        "input_schema": entry.input_schema,
        "output_schema": entry.output_schema,
    }


def set_tools(name: str, seen_hash: str, tools: list[dict[str, Any]]) -> None:
    """What the draft's server offered when it ran (launch.py): kept for the hash it ran with."""
    with session() as db:
        row = _row(db, name, "draft")
        if row is None or row.hash != seen_hash:
            raise ToolError("Черновик изменился, пока его проверяли: проверьте ещё раз")

        row.tools, row.tools_hash = tools, seen_hash
        db.commit()


def drop_draft(storage: StorageSettings, name: str) -> None:
    with _lock(name), session() as db:
        draft = _row(db, name, "draft")
        if draft is None:
            raise NotFound(f"У плагина «{name}» нет черновика")

        shutil.rmtree(plugin_dir(storage, name, "draft"), ignore_errors=True)
        db.delete(draft)
        db.commit()


def approve(storage: StorageSettings, name: str, seen_hash: str) -> dict[str, Any]:
    """The draft the person read (`seen_hash`) becomes the approved code. Tasks that call
    its tools stop until their user approves the new definitions for them."""
    with _lock(name):
        with session() as db:
            row = _row(db, name, "draft")

        if row is None:
            raise NotFound(f"У плагина «{name}» нет черновика")

        if row.hash != seen_hash:
            raise ToolError("Черновик изменился, пока вы его читали: посмотрите его ещё раз")

        if row.packages is None:
            raise ToolError("Пакеты черновика ещё не подобраны: подождите или сохраните его заново")

        if row.tools_hash != row.hash:
            raise ToolError("Сначала проверьте запуск: одобряют, увидев, какие инструменты отдаёт сервер")

        if files_hash(plugin_dir(storage, name, "draft")) != row.hash:
            raise ToolError("Файлы черновика изменились после сохранения: сохраните его заново и проверьте ещё раз")

        code.promote(storage.plugins_dir / name, storage.uv_cache_dir)

        with session() as db:
            db.query(Plugin).filter_by(name=name, status="current").delete()

            row = _row(db, name, "draft")
            row.status = "current"
            row.approved_at = utcnow()
            db.commit()

    return view(storage, name, "current")


def remove(storage: StorageSettings, name: str) -> None:
    """The plugin whole: its approved code and its draft."""
    with _lock(name), session() as db:
        if not db.query(Plugin).filter_by(name=name).count():
            raise NotFound(f"Плагина «{name}» нет")

        db.query(Plugin).filter_by(name=name).delete()
        db.commit()
        shutil.rmtree(storage.plugins_dir / name, ignore_errors=True)


def rows(which: Which) -> list[Plugin]:
    with session() as db:
        return db.query(Plugin).filter_by(status=which).order_by(Plugin.name).all()


def row(name: str, which: Which) -> Plugin | None:
    with session() as db:
        return _row(db, name, which)


def names() -> set[str]:
    with session() as db:
        return {name for (name,) in db.query(Plugin.name).distinct()}


def plugin_list() -> list[dict[str, Any]]:
    with session() as db:
        found = db.query(Plugin).order_by(Plugin.name).all()

    by_name: dict[str, dict[str, Plugin]] = {}
    for item in found:
        by_name.setdefault(item.name, {})[item.status] = item

    return [
        {
            "name": name,
            "title": ((states.get("current") or states["draft"]).manifest or {}).get("title"),
            "current": _summary(states["current"]) if "current" in states else None,
            "draft": _summary(states["draft"]) if "draft" in states else None,
        }
        for name, states in by_name.items()
    ]


def view(storage: StorageSettings, name: str, which: Which, *, files: bool = False) -> dict[str, Any]:
    with session() as db:
        found = _row(db, name, which)

    if found is None:
        raise NotFound(f"У плагина «{name}» нет {'черновика' if which == 'draft' else 'одобренного кода'}")

    result = {
        **_summary(found),
        "name": name,
        "manifest": found.manifest,
        "packages": found.packages,
        # The tools a person reads before approving: None — the server has not been run yet.
        "tools": found.tools if found.tools_hash == found.hash else None,
    }

    if files:
        result["files"], result["requirements"] = code.read(plugin_dir(storage, name, which), _RESERVED)

    return result


# -----------------------------
# CHECKS
# -----------------------------

def _check(
    name: str, manifest: dict[str, Any], files: dict[str, str], requirements: list[str], taken_groups: Collection[str]
) -> PluginManifest:
    problems: list[str] = []

    if not re.fullmatch(NAME_PATTERN, name):
        problems.append(f"Имя «{name}»: латиница в нижнем регистре, цифры и _, например telegram; это группа его инструментов")
    elif name in taken_groups:
        problems.append(f"Имя «{name}»: группа «{name}» занята встроенными командами; плагину нужна своя")

    checked = None
    try:
        checked = PluginManifest.model_validate(manifest)

    except ValidationError as e:
        problems += [f"{MANIFEST}, {'.'.join(str(p) for p in err['loc']) or 'целиком'}: {err['msg']}" for err in e.errors()]

    if checked is not None and checked.account is not None and checked.account.fields is not None:
        known = get_account_kind(checked.account.kind)
        if known is not None and known.owner != name:
            problems.append(f"account.kind «{checked.account.kind}»: этот вид аккаунта уже есть; свой назовите иначе или уберите fields")

    if checked is not None and checked.tools is not None:
        for entry in checked.tools:
            if entry.entry not in files:
                problems.append(f"Инструмент {entry.name}: нет файла {entry.entry} с функцией run(args, ctx)")

    problems += code.check_files(files, _RESERVED) + code.check_requirements(requirements)

    if problems:
        raise ToolError("Плагин не сохранён:\n- " + "\n- ".join(problems))

    return checked


# -----------------------------
# HELPERS
# -----------------------------

def _lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


def _row(db, name: str, which: Which) -> Plugin | None:
    return db.query(Plugin).filter_by(name=name, status=which).one_or_none()


def _summary(item: Plugin) -> dict[str, Any]:
    return {
        "status": item.status,
        "title": (item.manifest or {}).get("title"),
        "hash": item.hash,
        "created_by": item.created_by,
        # The chat that wrote the draft: its card is shown there.
        "session_id": item.session_id,
        "created_at": item.created_at.isoformat(),
        "approved_at": item.approved_at.isoformat() if item.approved_at else None,
    }
