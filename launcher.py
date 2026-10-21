"""Start the services as separate processes (Windows, Linux and macOS alike).

    python launcher.py               # all six services, logs in one console
    python launcher.py runner        # one service in the foreground (Docker uses this)
    python launcher.py --reload      # restart a service when its code changes

`.env` from the repository root is loaded into the environment first; variables
already set in the shell win.

All six services: main listens on MAIN_PORT, or else on the port it had last time, or
else on a free one picked at random; the port is kept in DATA_DIR/runtime.json with
main's address, for clients to find it. The other services get a free port from the
OS on every start and tell main where they are. One launcher per DATA_DIR: a second
one would run every task twice.

One service (Docker): its own PORT or the standard one, as compose expects."""

from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from common.settings import ROOT_DIR, SERVICE_PORTS, load_dotenv, path_coercion


# main starts first: every other service registers with it on startup.
START_ORDER = ["main", "executor", "agent_loop", "compiler", "runner", "scheduler"]

_RESTART_DELAY_SEC = 2
_MAX_RESTARTS_PER_MINUTE = 5
_MAIN_READY_TIMEOUT_SEC = 30

# Where main's random port is picked from: above the well-known ports and below the
# ephemeral ranges the OS hands out (Linux from 32768, Windows from 49152).
_MAIN_PORTS = (20000, 32767)


def main() -> None:
    parser = argparse.ArgumentParser(description="Office agent launcher")
    parser.add_argument("service", nargs="?", choices=START_ORDER, help="run only this service")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    args = parser.parse_args()

    env = _environment()

    if args.service:
        sys.exit(_run_single(args.service, env, args.reload))

    _run_all(env, args.reload)


def _environment() -> dict[str, str]:
    env = {**load_dotenv(ROOT_DIR / ".env"), **os.environ}

    # One token per launch is enough when all services are children of this process.
    if not env.get("INTERNAL_TOKEN"):
        env["INTERNAL_TOKEN"] = secrets.token_urlsafe(24)

    env.setdefault("PYTHONUNBUFFERED", "1")

    return env


def _command(service: str, env: dict[str, str], reload: bool, port: int | None = None) -> list[str]:
    if port is None:
        port = env.get("PORT") if env.get("SERVICE_NAME") == service else None

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        f"{service}.main:app",
        "--host",
        env.get("SERVICE_HOST", "127.0.0.1"),
        "--port",
        str(port or SERVICE_PORTS[service]),
        "--app-dir",
        str(ROOT_DIR),
        "--no-access-log",
    ]

    if reload:
        command += ["--reload", "--reload-dir", str(ROOT_DIR / service), "--reload-dir", str(ROOT_DIR / "common")]

    return command


def _service_env(service: str, env: dict[str, str], port: int) -> dict[str, str]:
    # PORT/SELF_URL from .env would apply to every child; each service gets its own.
    return {**env, "SERVICE_NAME": service, "PORT": str(port), "SELF_URL": f"http://127.0.0.1:{port}"}


def _run_single(service: str, env: dict[str, str], reload: bool) -> int:
    child_env = {**env, "SERVICE_NAME": service}

    return subprocess.call(_command(service, child_env, reload), env=child_env, cwd=ROOT_DIR)


class _Child:
    def __init__(self, service: str, env: dict[str, str], reload: bool, port: int | None = None):

        self.service  = service
        self.base_env = env
        self.reload   = reload
        # main keeps its port across restarts (the others know it); the rest take a free
        # one on every start, so a port taken meanwhile never stops a restart.
        self.port     = port
        self.process: subprocess.Popen | None = None
        self.restarts: list[float] = []


    def start(self) -> None:
        port = self.port or _free_port(self.base_env)
        env = _service_env(self.service, self.base_env, port)

        self.process = subprocess.Popen(
            _command(self.service, env, self.reload, port),
            env=env,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump, daemon=True).start()


    def _pump(self) -> None:
        prefix = f"[{self.service:<10}]"
        for line in self.process.stdout:
            sys.stdout.write(f"{prefix} {line}")
            sys.stdout.flush()


    def poll_and_restart(self) -> None:
        if self.process is None or self.process.poll() is None:
            return

        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < 60]

        if len(self.restarts) >= _MAX_RESTARTS_PER_MINUTE:
            return

        print(f"[launcher  ] {self.service} exited with {self.process.returncode}, restarting", flush=True)
        self.restarts.append(now)
        time.sleep(_RESTART_DELAY_SEC)
        self.start()


    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()


    def wait(self) -> None:
        if self.process is None:
            return

        try:
            self.process.wait(timeout=10)

        except subprocess.TimeoutExpired:
            self.process.kill()


def _run_all(env: dict[str, str], reload: bool) -> None:
    data_dir = path_coercion(env.get("DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    lock = _InstanceLock(data_dir / "launcher.lock")
    if not lock.acquire():
        running = _read_runtime(data_dir).get("main_url") or "address unknown"
        print(f"[launcher  ] already running with this DATA_DIR ({running}); a second launcher would run every task twice", flush=True)
        sys.exit(1)

    main_port = _main_port(data_dir, env)
    main_url = f"http://127.0.0.1:{main_port}"
    env = {**env, "MAIN_URL": main_url}

    children = [_Child(service, env, reload, port=main_port if service == "main" else None) for service in START_ORDER]
    stopping = threading.Event()

    def _stop(*_):
        stopping.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    children[0].start()
    _wait_for_main(main_url)
    _write_runtime(data_dir, {"main_url": main_url, "port": main_port, "pid": os.getpid()})

    for child in children[1:]:
        child.start()

    print(f"[launcher  ] started: {', '.join(START_ORDER)} · API at {main_url}", flush=True)

    try:
        while not stopping.is_set():
            for child in children:
                child.poll_and_restart()

            stopping.wait(1)

    finally:
        print("[launcher  ] stopping…", flush=True)
        for child in reversed(children):
            child.stop()

        for child in reversed(children):
            child.wait()

        # The port stays for the next start; the address is no longer served.
        _write_runtime(data_dir, {"main_url": None, "port": main_port, "pid": None})
        lock.release()


def _wait_for_main(main_url: str) -> None:
    url = f"{main_url}/health"
    deadline = time.monotonic() + _MAIN_READY_TIMEOUT_SEC

    # Plain urllib with no proxy: the launcher must not depend on project packages
    # beyond settings, and corporate proxies must not intercept localhost.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return

        except OSError:
            time.sleep(0.5)

    print("[launcher  ] main did not become healthy in time; starting the rest anyway", flush=True)


# -----------------------------
# PORTS
# -----------------------------

def _main_port(data_dir: Path, env: dict[str, str]) -> int:
    """MAIN_PORT, else last time's port if still free, else a random free one."""
    if env.get("MAIN_PORT"):
        port = env["MAIN_PORT"]
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise SystemExit(f"[launcher  ] MAIN_PORT must be a port number, got {port!r}")

        return int(port)

    host = env.get("SERVICE_HOST", "127.0.0.1")
    saved = _read_runtime(data_dir).get("port")

    if isinstance(saved, int) and _is_free(host, saved):
        return saved

    # A port that looks free may still refuse a bind (Windows reserves whole ranges for
    # Hyper-V): only a bind that works counts.
    for _ in range(100):
        port = random.randint(*_MAIN_PORTS)
        if _is_free(host, port):
            return port

    raise SystemExit("[launcher  ] no free port for main; set MAIN_PORT")


def _free_port(env: dict[str, str]) -> int:
    """A port the OS has free now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((env.get("SERVICE_HOST", "127.0.0.1"), 0))
        return sock.getsockname()[1]


def _is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # As uvicorn binds: on Windows SO_REUSEADDR would let two servers share a port.
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind((host, port))
            return True

        except OSError:
            return False


def _read_runtime(data_dir: Path) -> dict:
    try:
        return json.loads((data_dir / "runtime.json").read_text(encoding="utf-8"))

    except (OSError, ValueError):
        return {}


def _write_runtime(data_dir: Path, runtime: dict) -> None:
    path = data_dir / "runtime.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(runtime, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# -----------------------------
# ONE LAUNCHER PER DATA_DIR
# -----------------------------

class _InstanceLock:
    """An OS lock on a file: released by the OS when the launcher dies, however it dies."""

    def __init__(self, path: Path):

        self.file = open(path, "a+")


    def acquire(self) -> bool:
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)

            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            return True

        except OSError:
            return False


    def release(self) -> None:
        self.file.close()


if __name__ == "__main__":
    main()
