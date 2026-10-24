"""Versions of external tools: their folders and rows. Only the executor calls this.

A command has at most one draft, changed in place (its number stays) until a person
approves it; an approved version never changes again: its files must keep matching the
hash stored at approval, or it does not run. A person approves the draft they read: the
approval names its hash, and a draft rewritten since is not approved. Writing, throwing
away and approving one command's versions go one at a time."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from common.external_tools import NAME_RE, PREFIX, deps, full_name
from common.external_tools.manifest import Manifest
from common.settings import StorageSettings
from common.storage.db import session
from common.storage.models import ExternalTool, ExternalToolVersion
from common.tools import ToolError
from common.utils import new_id, utcnow


MANIFEST = "tool.json"
ENTRY = "main.py"

# What a version may carry besides its manifest and requirements: code and small data.
_FILE_SUFFIXES = (".py", ".json", ".txt", ".csv", ".md", ".yaml", ".yml")
_RESERVED = {MANIFEST, deps.REQUIREMENTS, deps.LOCK}
_MAX_FILE_BYTES = 1_000_000
_MAX_TOTAL_BYTES = 5_000_000

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def version_dir(storage: StorageSettings, name: str, version: int) -> Path:
    return storage.external_tools_dir / name / str(version)


def files_hash(folder: Path) -> str:
    """Over every file of the version (path and content), .venv aside: a __pycache__ the
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
) -> dict[str, Any]:
    """Write the command's draft (a new version number, or the draft there already is).
    Everything wrong is named at once, before anything is written. `only_own`: a draft
    someone else wrote (a person, another chat) is not replaced."""
    checked = _check(name, manifest, files, requirements)

    with _lock(name):
        with session() as db:
            draft = _draft(db, name)
            number = draft.version if draft else _next_version(db, name)

        if only_own and draft and (draft.created_by, draft.session_id) != (created_by, session_id):
            who = "другой чат" if draft.created_by == "chat" else "пользователь на странице «Команды»"
            raise ToolError(
                f"У `{full_name(name)}` уже есть черновик v{draft.version}: его написал {who}. "
                "Он не заменён: пусть пользователь одобрит или выбросит его, или выбери другое имя"
            )

        folder = version_dir(storage, name, number)
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
            if db.get(ExternalTool, name) is None:
                db.add(ExternalTool(name=name))

            row = _draft(db, name) or ExternalToolVersion(id=new_id("etv_"), name=name, version=number)
            row.status = "draft"
            row.hash = files_hash(folder)
            row.manifest = checked.model_dump()
            row.packages = packages
            row.created_by = created_by
            row.session_id = session_id
            db.add(row)
            db.commit()

    return version_view(storage, name, number)


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
        draft = _draft(db, name)
        if draft is None:
            raise ToolError(f"У `{full_name(name)}` нет черновика")

        shutil.rmtree(version_dir(storage, name, draft.version), ignore_errors=True)
        db.delete(draft)

        tool = db.get(ExternalTool, name)
        if tool is not None and tool.current_version is None:
            db.delete(tool)

        db.commit()


def approve(storage: StorageSettings, name: str, version: int, seen_hash: str) -> dict[str, Any]:
    """Freeze the draft the person read (`seen_hash`) and make it current. Its .venv is
    built anew: nothing a try of the draft left there runs in the approved version."""
    with _lock(name):
        with session() as db:
            row = _row(db, name, version)

        if row.status != "draft":
            raise ToolError(f"`{full_name(name)}` v{version} уже одобрена")

        if row.hash != seen_hash:
            raise ToolError("Черновик изменился, пока вы его читали: посмотрите его ещё раз")

        folder = version_dir(storage, name, version)
        if files_hash(folder) != row.hash:
            raise ToolError("Файлы черновика изменились после сохранения: сохраните его заново и проверьте ещё раз")

        deps.ensure_venv(folder, storage.uv_cache_dir, fresh=True)

        with session() as db:
            row = _row(db, name, version)
            row.status = "approved"
            row.approved_at = utcnow()

            tool = db.get(ExternalTool, name)
            tool.current_version = version
            db.commit()

    return version_view(storage, name, version)


def sweep_unused(storage: StorageSettings, needed: set[tuple[str, int]]) -> int:
    """Delete approved versions nothing runs any more: not current, and not in `needed`
    ({(ext.<name>, version)} that tasks and unfinished runs pin: their owners say which)."""
    with session() as db:
        current = {(t.name, t.current_version) for t in db.query(ExternalTool)}
        old = [
            row for row in db.query(ExternalToolVersion).filter_by(status="approved")
            if (row.name, row.version) not in current
        ]

    used = {(tool.removeprefix(PREFIX), version) for tool, version in needed}
    removed = 0

    for row in old:
        if (row.name, row.version) in used:
            continue

        shutil.rmtree(version_dir(storage, row.name, row.version), ignore_errors=True)

        with session() as db:
            db.query(ExternalToolVersion).filter_by(id=row.id).delete()
            db.commit()

        removed += 1

    return removed


def current_versions() -> list[ExternalToolVersion]:
    """The approved version each command runs now (for the executor's registry)."""
    with session() as db:
        tools = {t.name: t.current_version for t in db.query(ExternalTool) if t.current_version is not None}
        rows = db.query(ExternalToolVersion).filter(ExternalToolVersion.name.in_(list(tools))).all()

    return [row for row in rows if tools[row.name] == row.version]


def approved_version(name: str, version: int) -> ExternalToolVersion | None:
    with session() as db:
        return db.query(ExternalToolVersion).filter_by(name=name, version=version, status="approved").one_or_none()


def tool_list() -> list[dict[str, Any]]:
    with session() as db:
        tools = db.query(ExternalTool).order_by(ExternalTool.name).all()
        rows = db.query(ExternalToolVersion).order_by(ExternalToolVersion.version).all()

    by_name: dict[str, list[ExternalToolVersion]] = {}
    for row in rows:
        by_name.setdefault(row.name, []).append(row)

    return [
        {
            "name": tool.name,
            "tool": full_name(tool.name),
            "title": (_current(by_name.get(tool.name, []), tool.current_version) or {}).get("title"),
            "current_version": tool.current_version,
            "draft_version": next((r.version for r in by_name.get(tool.name, []) if r.status == "draft"), None),
            "versions": [_summary(r) for r in by_name.get(tool.name, [])],
        }
        for tool in tools
    ]


def version_view(storage: StorageSettings, name: str, version: int, *, files: bool = False) -> dict[str, Any]:
    with session() as db:
        row = _row(db, name, version)
        tool = db.get(ExternalTool, name)

    view = {
        **_summary(row),
        "name": name,
        "tool": full_name(name),
        "current": tool is not None and tool.current_version == version,
        "manifest": row.manifest,
        "packages": row.packages,
        "hash": row.hash,
    }

    if files:
        # The code apart from the version's own bookkeeping (tool.json is `manifest`, the
        # lock is `packages`): whoever shows or edits it need not know the folder's layout.
        folder = version_dir(storage, name, version)
        every = {
            path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(folder.rglob("*"))
            if path.is_file() and not _ignored(path.relative_to(folder))
        }
        view["entry"] = ENTRY
        view["files"] = {path: text for path, text in every.items() if path not in _RESERVED}
        view["requirements"] = [line.strip() for line in every.get(deps.REQUIREMENTS, "").splitlines() if line.strip()]

    return view


# -----------------------------
# CHECKS
# -----------------------------

def _check(name: str, manifest: dict[str, Any], files: dict[str, str], requirements: list[str]) -> Manifest:
    problems: list[str] = []

    if not NAME_RE.match(name):
        problems.append(f"Имя «{name}»: латиницей в нижнем регистре, цифры и _, от 2 до 41 знака, например parse_invoice")

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


def _draft(db, name: str) -> ExternalToolVersion | None:
    return db.query(ExternalToolVersion).filter_by(name=name, status="draft").one_or_none()


def _next_version(db, name: str) -> int:
    numbers = [row.version for row in db.query(ExternalToolVersion).filter_by(name=name)]

    return max(numbers, default=0) + 1


def _row(db, name: str, version: int) -> ExternalToolVersion:
    row = db.query(ExternalToolVersion).filter_by(name=name, version=version).one_or_none()
    if row is None:
        raise ToolError(f"Нет версии v{version} у `{full_name(name)}`")

    return row


def _current(rows: list[ExternalToolVersion], version: int | None) -> dict[str, Any] | None:
    row = next((r for r in rows if r.version == version), None) or (rows[-1] if rows else None)

    return row.manifest if row else None


def _summary(row: ExternalToolVersion) -> dict[str, Any]:
    return {
        "version": row.version,
        "status": row.status,
        "title": (row.manifest or {}).get("title"),
        "created_by": row.created_by,
        # The chat that wrote the version: its card is shown there.
        "session_id": row.session_id,
        "created_at": row.created_at.isoformat(),
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
    }
