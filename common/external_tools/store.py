"""Own commands: their folders and rows. Only the executor calls this.

A command has its current code (approved, it runs) and at most one draft, changed in
place until a person approves it. The approved code never changes: its files must keep
matching the hash stored at approval, or it does not run. A person approves the draft
they read: the approval names its hash, and a draft rewritten since is not approved;
the draft then takes the current code's place. Writing, throwing away and approving
one command's draft go one at a time."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any, Collection, Literal

from pydantic import ValidationError

from common.external_tools import NAME_RE, deps
from common.external_tools.manifest import Manifest
from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import ExternalTool
from common.tools import ToolError
from common.tools.settings import tool_group
from common.utils import new_id, utcnow


Which = Literal["current", "draft"]

MANIFEST = "tool.json"
ENTRY = "main.py"

# What a command may carry besides its manifest and requirements: code and small data.
_FILE_SUFFIXES = (".py", ".json", ".txt", ".csv", ".md", ".yaml", ".yml")
_RESERVED = {MANIFEST, deps.REQUIREMENTS, deps.LOCK}
_MAX_FILE_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 5_000_000

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def tool_dir(storage: StorageSettings, name: str, which: Which) -> Path:
    """`telegram.send`'s draft → external_tools/telegram/send/draft."""
    group, action = name.split(".", 1)
    return storage.external_tools_dir / group / action / which


def files_hash(folder: Path) -> str:
    """Over every file of the command (path and content), .venv aside: a __pycache__ the
    code left there counts too, so compiled code cannot run in place of what was read."""
    digest = hashlib.sha256()

    for path in sorted(p for p in folder.rglob("*") if p.is_file() and not _ignored(p.relative_to(folder))):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")

    return digest.hexdigest()


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
    builtin_groups: Collection[str] = (),
) -> dict[str, Any]:
    """Write the command's draft (a new one, or over the draft there already is).
    Everything wrong is named at once, before anything is written. `only_own`: a draft
    someone else wrote (a person, another chat) is not replaced. `builtin_groups`: the
    groups of the built-in commands, which an own command cannot join."""
    checked = _check(name, manifest, files, requirements, builtin_groups)

    with _lock(name):
        with session() as db:
            draft = _row(db, name, "draft")

        if only_own and draft and (draft.created_by, draft.session_id) != (created_by, session_id):
            who = "другой чат" if draft.created_by == "chat" else "пользователь на странице «Команды»"
            raise ToolError(
                f"У `{name}` уже есть черновик: его написал {who}. "
                "Он не заменён: пусть пользователь одобрит или выбросит его, или выбери другое имя"
            )

        folder = tool_dir(storage, name, "draft")
        # Packages first, aside: when they do not resolve, the draft stays as it was.
        lock_text, packages = _resolve(folder, requirements, storage)
        _clear(folder)

        for rel, text in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

        (folder / MANIFEST).write_text(json.dumps(checked.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        (folder / deps.REQUIREMENTS).write_text("".join(f"{line}\n" for line in requirements), encoding="utf-8")
        (folder / deps.LOCK).write_text(lock_text, encoding="utf-8")

        with session() as db:
            row = _row(db, name, "draft") or ExternalTool(id=new_id("etv_"), name=name, status="draft")
            row.hash = files_hash(folder)
            row.manifest = checked.model_dump()
            row.packages = packages
            row.created_by = created_by
            row.session_id = session_id
            db.add(row)
            db.commit()

    return view(storage, name, "draft")


def _resolve(folder: Path, requirements: list[str], storage: StorageSettings) -> tuple[str, list[str]]:
    """requirements → (requirements.lock, its packages), in a folder of its own."""
    staging = folder.with_name(f".{folder.name}.lock")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    try:
        (staging / deps.REQUIREMENTS).write_text("".join(f"{line}\n" for line in requirements), encoding="utf-8")
        packages = deps.lock(staging, storage.uv_cache_dir)

        return (staging / deps.LOCK).read_text(encoding="utf-8"), packages

    finally:
        shutil.rmtree(staging, ignore_errors=True)


def drop_draft(storage: StorageSettings, name: str) -> None:
    with _lock(name), session() as db:
        draft = _row(db, name, "draft")
        if draft is None:
            raise ToolError(f"У `{name}` нет черновика")

        shutil.rmtree(tool_dir(storage, name, "draft"), ignore_errors=True)
        db.delete(draft)
        db.commit()


def approve(storage: StorageSettings, name: str, seen_hash: str) -> dict[str, Any]:
    """The draft the person read (`seen_hash`) becomes the current code. Its .venv is
    built anew: nothing a try of the draft left there runs in the approved code. Tasks
    that call the command stop until their user approves the new code for them."""
    with _lock(name):
        with session() as db:
            row = _row(db, name, "draft")

        if row is None:
            raise ToolError(f"У `{name}` нет черновика")

        if row.hash != seen_hash:
            raise ToolError("Черновик изменился, пока вы его читали: посмотрите его ещё раз")

        draft, current = tool_dir(storage, name, "draft"), tool_dir(storage, name, "current")
        if files_hash(draft) != row.hash:
            raise ToolError("Файлы черновика изменились после сохранения: сохраните его заново и проверьте ещё раз")

        shutil.rmtree(current, ignore_errors=True)
        draft.rename(current)
        deps.ensure_venv(current, storage.uv_cache_dir, fresh=True)

        with session() as db:
            db.query(ExternalTool).filter_by(name=name, status="current").delete()

            row = _row(db, name, "draft")
            row.status = "current"
            row.approved_at = utcnow()
            db.commit()

    return view(storage, name, "current")


def current_rows() -> list[ExternalTool]:
    """The approved code of every command (for the executor's registry)."""
    with session() as db:
        return db.query(ExternalTool).filter_by(status="current").all()


def draft_row(name: str) -> ExternalTool | None:
    with session() as db:
        return _row(db, name, "draft")


def tool_list() -> list[dict[str, Any]]:
    with session() as db:
        rows = db.query(ExternalTool).order_by(ExternalTool.name).all()

    by_name: dict[str, dict[str, ExternalTool]] = {}
    for row in rows:
        by_name.setdefault(row.name, {})[row.status] = row

    return [
        {
            "name": name,
            "title": ((found.get("current") or found["draft"]).manifest or {}).get("title"),
            "current": _summary(found["current"]) if "current" in found else None,
            "draft": _summary(found["draft"]) if "draft" in found else None,
        }
        for name, found in by_name.items()
    ]


def view(storage: StorageSettings, name: str, which: Which, *, files: bool = False) -> dict[str, Any]:
    with session() as db:
        row = _row(db, name, which)

    if row is None:
        raise ToolError(f"У `{name}` нет {'черновика' if which == 'draft' else 'одобренного кода'}")

    found = {
        **_summary(row),
        "name": name,
        "manifest": row.manifest,
        "packages": row.packages,
    }

    if files:
        # The code apart from the command's own bookkeeping (tool.json is `manifest`, the
        # lock is `packages`): whoever shows or edits it need not know the folder's layout.
        folder = tool_dir(storage, name, which)
        every = {
            path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(folder.rglob("*"))
            if path.is_file() and not _ignored(path.relative_to(folder))
        }
        found["entry"] = ENTRY
        found["files"] = {path: text for path, text in every.items() if path not in _RESERVED}
        found["requirements"] = [line.strip() for line in every.get(deps.REQUIREMENTS, "").splitlines() if line.strip()]

    return found


# -----------------------------
# CHECKS
# -----------------------------

def _check(name: str, manifest: dict[str, Any], files: dict[str, str], requirements: list[str], builtin_groups: Collection[str]) -> Manifest:
    problems: list[str] = []

    if not NAME_RE.match(name):
        problems.append(f"Имя «{name}»: группа.действие латиницей в нижнем регистре, цифры и _, например telegram.send")
    elif tool_group(name) in builtin_groups:
        problems.append(f"Имя «{name}»: группа «{tool_group(name)}» встроенная; своей команде нужна своя группа, например telegram.send")

    checked = None
    try:
        checked = Manifest.model_validate(manifest)

    except ValidationError as e:
        problems += [f"tool.json, {'.'.join(str(p) for p in err['loc']) or 'целиком'}: {err['msg']}" for err in e.errors()]

    if ENTRY not in files:
        problems.append(f"Нет {ENTRY}: в нём должна быть функция run(args, ctx)")

    total = 0
    for rel, text in files.items():
        parts = Path(rel).parts
        total += len(text.encode("utf-8"))

        if Path(rel).is_absolute() or ".." in parts or rel.startswith(("/", "\\")) or not parts:
            problems.append(f"Файл «{rel}»: только путь внутри папки команды")
        elif parts[0] in _RESERVED or parts[0] == deps.VENV or "__pycache__" in parts:
            problems.append(f"Файл «{rel}»: это имя занято ({', '.join(sorted(_RESERVED))}, {deps.VENV}, __pycache__)")
        elif not rel.endswith(_FILE_SUFFIXES):
            problems.append(f"Файл «{rel}»: допустимы {', '.join(_FILE_SUFFIXES)}")
        elif len(text.encode("utf-8")) > _MAX_FILE_BYTES:
            problems.append(f"Файл «{rel}» больше {_MAX_FILE_BYTES // 1_000_000} МБ")

        if rel.endswith(".py"):
            try:
                compile(text, rel, "exec")

            except SyntaxError as e:
                problems.append(f"{rel}, строка {e.lineno}: {e.msg}")

    if total > _MAX_TOTAL_BYTES:
        problems.append(f"Файлы вместе больше {_MAX_TOTAL_BYTES // 1_000_000} МБ")

    for line in requirements:
        # One package per item: a line break would smuggle in an option such as `-r <file>`.
        if any(ch < " " for ch in line) or not line.strip() or line.strip().startswith(("-", "#", ".", "/", "\\")) or "://" in line:
            problems.append(f"Зависимость «{line}»: только пакет из индекса, например pandas>=2")

    if problems:
        raise ToolError("Команда не сохранена:\n- " + "\n- ".join(problems))

    return checked


# -----------------------------
# HELPERS
# -----------------------------

def _ignored(rel: Path) -> bool:
    return rel.parts[0] == deps.VENV


def _lock(name: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(name, threading.Lock())


def _clear(folder: Path) -> None:
    """Empty a draft's folder for its new files, keeping its .venv for reuse."""
    folder.mkdir(parents=True, exist_ok=True)

    for path in folder.iterdir():
        if path.name == deps.VENV:
            continue

        shutil.rmtree(path) if path.is_dir() else path.unlink()


def _row(db, name: str, which: Which) -> ExternalTool | None:
    return db.query(ExternalTool).filter_by(name=name, status=which).one_or_none()


def _summary(row: ExternalTool) -> dict[str, Any]:
    return {
        "status": row.status,
        "title": (row.manifest or {}).get("title"),
        "hash": row.hash,
        "created_by": row.created_by,
        # The chat that wrote the draft: its card is shown there.
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat(),
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
    }
