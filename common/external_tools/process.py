"""One call of an external tool: its .venv's python runs shim.py in a process of its own.

A plain subprocess in a thread, not asyncio's: the event loop uvicorn runs on Windows
cannot start subprocesses. The process gets a cleaned environment (no tokens, keys or
proxies of the agent) and is killed at the timeout."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from common.tools import ToolError


SHIM = Path(__file__).with_name("shim.py")

# What a tool may answer; more is a broken tool, not a result.
_MAX_ANSWER_BYTES = 20_000_000
_MAX_STDERR_CHARS = 20_000

# Passed on so the interpreter and OS work as usual; everything else stays behind.
_KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
             "LANG", "LC_ALL", "LC_CTYPE", "TZ")


def run(python: Path, version_dir: Path, request: dict[str, Any], timeout: float) -> dict[str, Any]:
    """The shim's answer; a crash of the process itself becomes an error answer."""
    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ}

    # -I ignores PYTHON* variables (and the user's site), so the rest are flags: UTF-8
    # whatever the OS locale (-X utf8), no __pycache__ in the frozen folder (-B).
    process = subprocess.Popen(
        [str(python), "-I", "-B", "-X", "utf8", str(SHIM), str(version_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=version_dir,
        env=env,
    )

    try:
        stdout, stderr = process.communicate(json.dumps(request, ensure_ascii=False).encode("utf-8"), timeout=timeout)

    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError from None

    printed = stderr.decode("utf-8", errors="replace").strip()[-_MAX_STDERR_CHARS:]

    if len(stdout) > _MAX_ANSWER_BYTES:
        raise ToolError(f"Команда ответила больше {_MAX_ANSWER_BYTES // 1_000_000} МБ")

    try:
        answer = json.loads(stdout.decode("utf-8"))

    except (UnicodeDecodeError, json.JSONDecodeError):
        answer = {"ok": False, "error": f"Процесс команды завершился с кодом {process.returncode} без ответа"}

    answer["printed"] = printed

    return answer
