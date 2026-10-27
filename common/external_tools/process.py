"""One call of an external tool: its .venv's python runs shim.py in a process of its own.

A plain subprocess in a thread, not asyncio's: the event loop uvicorn runs on Windows
cannot start subprocesses. The process gets a cleaned environment (no tokens, keys or
proxies of the agent) and a process group of its own: at the timeout, or when it prints
more than a tool may answer, the whole group is killed, children it started included."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from common.tools import ToolError


SHIM = Path(__file__).with_name("shim.py")

# What a tool may answer; more is a broken tool, not a result.
_MAX_ANSWER_BYTES = 20_000_000
_MAX_STDERR_BYTES = 80_000
# After a kill: how long the pipes may take to close before they are left behind.
_DRAIN_SEC = 5

# Passed on so the interpreter and OS work as usual; everything else stays behind.
_KEEP_ENV = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
             "LANG", "LC_ALL", "LC_CTYPE", "TZ")


def run(python: Path, folder: Path, request: dict[str, Any], timeout: float) -> dict[str, Any]:
    """The shim's answer; a crash of the process itself becomes an error answer."""
    env = {key: os.environ[key] for key in _KEEP_ENV if key in os.environ}
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}

    # -I ignores PYTHON* variables (and the user's site), so the rest are flags: UTF-8
    # whatever the OS locale (-X utf8), no __pycache__ in the frozen folder (-B).
    process = subprocess.Popen(
        [str(python), "-I", "-B", "-X", "utf8", str(SHIM), str(folder)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=folder,
        env=env,
        **group,
    )

    stdout, stderr = bytearray(), bytearray()
    too_big = threading.Event()
    # Every pipe in a thread of its own: a process that reads or prints nothing must still
    # meet its timeout.
    pipes = [
        threading.Thread(target=_write, args=(process.stdin, json.dumps(request, ensure_ascii=False).encode("utf-8")), daemon=True),
        threading.Thread(target=_read_answer, args=(process, stdout, too_big), daemon=True),
        threading.Thread(target=_read_tail, args=(process.stderr, stderr), daemon=True),
    ]
    for pipe in pipes:
        pipe.start()

    try:
        process.wait(timeout=timeout)

    except subprocess.TimeoutExpired:
        _kill(process)
        raise TimeoutError from None

    finally:
        # Children it left running hold the pipes open: they go with the group.
        _kill(process)
        for pipe in pipes:
            pipe.join(_DRAIN_SEC)

    if too_big.is_set():
        raise ToolError(f"Команда ответила больше {_MAX_ANSWER_BYTES // 1_000_000} МБ")

    try:
        answer = json.loads(stdout.decode("utf-8"))

    except (UnicodeDecodeError, json.JSONDecodeError):
        answer = None

    if not isinstance(answer, dict):
        answer = {"ok": False, "error": f"Процесс команды завершился с кодом {process.returncode} без ответа"}

    answer["printed"] = stderr.decode("utf-8", errors="replace").strip()

    return answer


def _write(stream, data: bytes) -> None:
    try:
        stream.write(data)
        stream.close()

    except OSError:
        pass  # It exited without reading: its exit code says why.


def _read_answer(process: subprocess.Popen, into: bytearray, too_big: threading.Event) -> None:
    while chunk := process.stdout.read(65536):
        into.extend(chunk)

        if len(into) > _MAX_ANSWER_BYTES:
            too_big.set()
            _kill(process)
            return


def _read_tail(stream, into: bytearray) -> None:
    """What the tool printed: only its last part is kept, however much it prints."""
    while chunk := stream.read(65536):
        into.extend(chunk)
        del into[:-_MAX_STDERR_BYTES]


def _kill(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)

    except (ProcessLookupError, PermissionError, OSError):
        pass
