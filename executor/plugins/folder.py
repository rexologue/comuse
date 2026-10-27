"""A plugin's folder: code a person approves.

Its files are checked before anything is written (only inside the folder, allowed
kinds, size, Python that compiles), its packages are resolved into a lock in a folder
aside (deps.py), and its hash covers every file but the .venv — a __pycache__ the code
left there counts too, so compiled code cannot run in place of what was read."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Collection, Literal

from executor.plugins import deps


# The approved code (it runs) and the draft.
Which = Literal["current", "draft"]

# What a folder may carry besides its manifest and requirements: code and small data.
FILE_SUFFIXES = (".py", ".json", ".txt", ".csv", ".md", ".yaml", ".yml", ".toml")
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 5_000_000


def files_hash(folder: Path) -> str:
    digest = hashlib.sha256()

    for path in sorted(p for p in folder.rglob("*") if p.is_file() and not ignored(p.relative_to(folder))):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")

    return digest.hexdigest()


def ignored(rel: Path) -> bool:
    return rel.parts[0] == deps.VENV


def check_files(files: dict[str, str], reserved: Collection[str]) -> list[str]:
    problems: list[str] = []
    total = 0

    for rel, text in files.items():
        parts = Path(rel).parts
        total += len(text.encode("utf-8"))

        if Path(rel).is_absolute() or ".." in parts or rel.startswith(("/", "\\")) or not parts:
            problems.append(f"Файл «{rel}»: только путь внутри папки")
        elif parts[0] in reserved or parts[0] == deps.VENV or "__pycache__" in parts:
            problems.append(f"Файл «{rel}»: это имя занято ({', '.join(sorted(reserved))}, {deps.VENV}, __pycache__)")
        elif not rel.endswith(FILE_SUFFIXES):
            problems.append(f"Файл «{rel}»: допустимы {', '.join(FILE_SUFFIXES)}")
        elif len(text.encode("utf-8")) > MAX_FILE_BYTES:
            problems.append(f"Файл «{rel}» больше {MAX_FILE_BYTES // 1_000_000} МБ")

        if rel.endswith(".py"):
            try:
                compile(text, rel, "exec")

            except SyntaxError as e:
                problems.append(f"{rel}, строка {e.lineno}: {e.msg}")

    if total > MAX_TOTAL_BYTES:
        problems.append(f"Файлы вместе больше {MAX_TOTAL_BYTES // 1_000_000} МБ")

    return problems


def check_requirements(requirements: list[str]) -> list[str]:
    # One package per item: a line break would smuggle in an option such as `-r <file>`.
    return [
        f"Зависимость «{line}»: только пакет из индекса, например pandas>=2"
        for line in requirements
        if any(ch < " " for ch in line) or not line.strip() or line.strip().startswith(("-", "#", ".", "/", "\\")) or "://" in line
    ]


def resolve(folder: Path, requirements: list[str], cache_dir: Path) -> tuple[str, list[str]]:
    """requirements → (requirements.lock, its packages), in a folder of its own: when they
    do not resolve, the folder stays as it was."""
    staging = folder.with_name(f".{folder.name}.lock")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    try:
        (staging / deps.REQUIREMENTS).write_text("".join(f"{line}\n" for line in requirements), encoding="utf-8")
        packages = deps.lock(staging, cache_dir)

        return (staging / deps.LOCK).read_text(encoding="utf-8"), packages

    finally:
        shutil.rmtree(staging, ignore_errors=True)


def write(folder: Path, files: dict[str, str], *, manifest_name: str, manifest_text: str, requirements: list[str], lock_text: str) -> None:
    """The folder's files anew; its .venv is kept for reuse."""
    folder.mkdir(parents=True, exist_ok=True)

    for path in folder.iterdir():
        if path.name != deps.VENV:
            shutil.rmtree(path) if path.is_dir() else path.unlink()

    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    (folder / manifest_name).write_text(manifest_text, encoding="utf-8")
    (folder / deps.REQUIREMENTS).write_text("".join(f"{line}\n" for line in requirements), encoding="utf-8")
    (folder / deps.LOCK).write_text(lock_text, encoding="utf-8")


def read(folder: Path, reserved: Collection[str]) -> tuple[dict[str, str], list[str]]:
    """(the code, the requirements) as a person reads them: the folder's bookkeeping
    files aside (the manifest, the lock)."""
    every = {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not ignored(path.relative_to(folder))
    }
    requirements = [line.strip() for line in every.get(deps.REQUIREMENTS, "").splitlines() if line.strip()]

    return {path: text for path, text in every.items() if path not in reserved}, requirements


def promote(storage_dir: Path, cache_dir: Path) -> None:
    """The draft folder becomes the current one (the old current goes), with its .venv
    built anew: nothing a try of the draft left there runs in the approved code."""
    draft, current = storage_dir / "draft", storage_dir / "current"

    shutil.rmtree(current, ignore_errors=True)
    draft.rename(current)
    deps.ensure_venv(current, cache_dir, fresh=True)
