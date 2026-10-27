"""Packages of an own command, through uv (a dependency of the project, so it is
wherever the agent is): requirements.txt → requirements.lock → the command's .venv.

Only wheels are installed (`--only-binary :all:`): installing a wheel runs no code of
the package, building one from source would. The lock pins exact versions with hashes
and is part of the code, so the person approving it sees every package, transitive
ones included. Installs share uv's cache in DATA_DIR, so the same package in another
command or draft costs next to nothing."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from common.tools import ToolError


LOCK = "requirements.lock"
REQUIREMENTS = "requirements.txt"
VENV = ".venv"

# Written into .venv when the lock is installed: a changed lock means a fresh .venv.
_MARKER = ".installed"
_UV_TIMEOUT_SEC = 900

# One install per .venv at a time: two first calls must not build it over each other.
_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def lock(folder: Path, cache_dir: Path) -> list[str]:
    """Resolve requirements.txt into requirements.lock; the exact packages, "name==version"."""
    requirements = (folder / REQUIREMENTS).read_text(encoding="utf-8") if (folder / REQUIREMENTS).is_file() else ""

    if not _lines(requirements):
        (folder / LOCK).write_text("", encoding="utf-8")
        return []

    _uv(
        ["pip", "compile", REQUIREMENTS, "-o", LOCK, "--generate-hashes", "--only-binary", ":all:",
         "--python", sys.executable, "--no-header", "--quiet"],
        cwd=folder,
        cache_dir=cache_dir,
        what="Не удалось подобрать пакеты",
    )

    return packages(folder)


def packages(folder: Path) -> list[str]:
    path = folder / LOCK
    text = path.read_text(encoding="utf-8") if path.is_file() else ""

    return [line.split()[0] for line in _lines(text) if "==" in line.split()[0]]


def python_of(folder: Path) -> Path:
    venv = folder / VENV
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def ensure_venv(folder: Path, cache_dir: Path, *, fresh: bool = False) -> Path:
    """The command's .venv with its lock installed (made on first use, or anew when
    `fresh`); its python."""
    with _LOCKS_GUARD:
        guard = _LOCKS.setdefault(folder.resolve(), threading.Lock())

    with guard:
        if fresh:
            shutil.rmtree(folder / VENV, ignore_errors=True)

        return _ensure_venv(folder, cache_dir)


def _ensure_venv(folder: Path, cache_dir: Path) -> Path:
    lock_path = folder / LOCK
    lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
    digest = hashlib.sha256(lock_text.encode()).hexdigest()

    venv = folder / VENV
    marker = venv / _MARKER
    if marker.is_file() and marker.read_text(encoding="utf-8") == digest and python_of(folder).exists():
        return python_of(folder)

    shutil.rmtree(venv, ignore_errors=True)
    _uv(["venv", VENV, "--python", sys.executable, "--quiet"], cwd=folder, cache_dir=cache_dir, what="Не удалось создать окружение")

    if _lines(lock_text):
        _uv(
            ["pip", "sync", LOCK, "--python", str(python_of(folder)), "--require-hashes",
             "--only-binary", ":all:", "--quiet"],
            cwd=folder,
            cache_dir=cache_dir,
            what="Не удалось установить пакеты",
        )

    marker.write_text(digest, encoding="utf-8")

    return python_of(folder)


def uv_bin() -> str:
    try:
        from uv import find_uv_bin

        return find_uv_bin()

    except (ImportError, FileNotFoundError):
        found = shutil.which("uv")
        if found is None:
            raise ToolError("Не найден uv: он нужен для окружений внешних команд") from None

        return found


def _uv(args: list[str], *, cwd: Path, cache_dir: Path, what: str) -> None:
    # The executor's own environment: proxies and UV_INDEX_URL (a corporate mirror) apply.
    env = {**os.environ, "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(cache_dir)}

    try:
        done = subprocess.run(
            [uv_bin(), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=_UV_TIMEOUT_SEC
        )

    except subprocess.TimeoutExpired:
        raise ToolError(f"{what}: uv не уложился в {_UV_TIMEOUT_SEC} с") from None

    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[-2000:]
        raise ToolError(f"{what}:\n{detail}")


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(("#", "--hash", "\\"))]
