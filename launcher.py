"""Start the services as separate processes (Windows, Linux and macOS alike).

    python launcher.py               # all six services, logs in one console
    python launcher.py runner        # one service in the foreground (Docker uses this)
    python launcher.py --reload      # restart a service when its code changes

`.env` from the repository root is loaded into the environment first; variables
already set in the shell win."""

from __future__ import annotations

import argparse
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request

from common.settings import ROOT_DIR, SERVICE_PORTS, load_dotenv


# main starts first: every other service registers with it on startup.
START_ORDER = ["main", "executor", "agent_loop", "compiler", "runner", "scheduler"]

_RESTART_DELAY_SEC = 2
_MAX_RESTARTS_PER_MINUTE = 5
_MAIN_READY_TIMEOUT_SEC = 30


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


def _command(service: str, env: dict[str, str], reload: bool) -> list[str]:
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


def _service_env(service: str, env: dict[str, str]) -> dict[str, str]:
    child = {**env, "SERVICE_NAME": service}

    # PORT/SELF_URL from .env would apply to every child; each service needs its own.
    if env.get("SERVICE_NAME") != service:
        child.pop("PORT", None)
        child.pop("SELF_URL", None)

    return child


def _run_single(service: str, env: dict[str, str], reload: bool) -> int:
    child_env = {**env, "SERVICE_NAME": service}

    return subprocess.call(_command(service, child_env, reload), env=child_env, cwd=ROOT_DIR)


class _Child:
    def __init__(self, service: str, env: dict[str, str], reload: bool):

        self.service  = service
        self.env      = _service_env(service, env)
        self.reload   = reload
        self.process: subprocess.Popen | None = None
        self.restarts: list[float] = []


    def start(self) -> None:
        self.process = subprocess.Popen(
            _command(self.service, self.env, self.reload),
            env=self.env,
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
    children = [_Child(service, env, reload) for service in START_ORDER]
    stopping = threading.Event()

    def _stop(*_):
        stopping.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    children[0].start()
    _wait_for_main()

    for child in children[1:]:
        child.start()

    print(f"[launcher  ] started: {', '.join(START_ORDER)} · API at http://127.0.0.1:{SERVICE_PORTS['main']}", flush=True)

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


def _wait_for_main() -> None:
    url = f"http://127.0.0.1:{SERVICE_PORTS['main']}/health"
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


if __name__ == "__main__":
    main()
