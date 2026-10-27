"""Shared settings for every service, read from environment variables: what a service
needs before the database opens. Settings the user changes while the agent runs live in
common/tunables.py."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class SettingsError(Exception):
    """Raised when service configuration is invalid."""


ROOT_DIR = Path(__file__).resolve().parent.parent

# RUN_MODE=single: every service but main is mounted under this prefix on main's port
# (single_app.py).
SINGLE_PREFIX = "/svc"

# Every variable the services read here (the working settings add theirs in
# common/tunables.py), for the check that no value is a leftover comment.
ENV_NAMES = (
    "SERVICE_NAME", "SERVICE_HOST", "PORT", "MAIN_URL", "RUN_MODE", "SELF_URL", "MAIN_PORT",
    "INTERNAL_TOKEN", "API_TOKEN", "ALLOWED_HOSTS", "ALLOWED_ORIGINS", "DATA_DIR", "LOG_DIR", "LOG_LEVEL",
    "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_CHAT", "LLM_MODEL_INSTRUCT",
    "LLM_MODEL_EMBED", "LLM_EMBED_BASE_URL", "LLM_EMBED_API_KEY", "KEYRING_BACKEND", "KEYRING_PASSWORD",
)

SERVICE_PORTS = {
    "main": 8000,
    "agent_loop": 8001,
    "compiler": 8002,
    "runner": 8003,
    "executor": 8004,
    "scheduler": 8005,
}


@dataclass(slots=True, frozen=True)
class ServiceSettings:
    name: str
    host: str
    port: int
    self_url: str
    main_url: str
    internal_token: str
    # Empty: main makes one and keeps it in DATA_DIR/api_token (main/auth.py).
    api_token: str
    # main only: Host headers it answers (DNS rebinding) and origins allowed by CORS.
    allowed_hosts: list[str]
    allowed_origins: list[str]
    # multi: a process per service; single: all of them in one (single_app.py).
    run_mode: str


@dataclass(slots=True, frozen=True)
class StorageSettings:
    data_dir: Path
    # Log files (common/service.py): DATA_DIR/logs unless LOG_DIR says otherwise.
    log_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"


    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"


    @property
    def plugins_dir(self) -> Path:
        """<group>/{current,draft}/: the plugins' files (executor/plugins)."""
        return self.data_dir / "plugins"


    @property
    def external_tools_dir(self) -> Path:
        """Where own commands lived before they were plugins: only old migrations read it."""
        return self.data_dir / "external_tools"


    @property
    def uv_cache_dir(self) -> Path:
        """uv's package cache: next to the plugins' .venv folders, so installs hardlink."""
        return self.data_dir / "cache" / "uv"


@dataclass(slots=True, frozen=True)
class LLMSettings:
    """Models from LLM_* variables; main registers them as providers on start."""
    base_url: str
    api_key: str
    model_chat: str
    model_instruct: str
    # Empty = no embeddings model.
    model_embed: str
    # Embeddings may live on another server than chat (defaults to the same one).
    embed_base_url: str
    embed_api_key: str


@dataclass(slots=True, frozen=True)
class SecretsSettings:
    keyring_backend: str
    keyring_password: str


@dataclass(slots=True, frozen=True)
class Settings:
    service: ServiceSettings
    storage: StorageSettings
    # None: no LLM_MODEL in the environment, nothing to register.
    llm: LLMSettings | None
    secrets: SecretsSettings

    @classmethod
    def from_env(cls, service_name: str | None = None) -> Settings:
        name = service_name or os.environ.get("SERVICE_NAME", "main")
        if name not in SERVICE_PORTS:
            raise SettingsError(
                f"Unknown `SERVICE_NAME`: {name!r}. Must be one of {list(SERVICE_PORTS)}."
            )

        # -----------------------------
        # SERVICE
        # -----------------------------

        service_host = os.environ.get("SERVICE_HOST", "127.0.0.1")

        service_port = int_coercion(
            os.environ.get("PORT", str(SERVICE_PORTS[name])),
            "PORT",
            low=1,
            high=65535,
        )

        main_url = os.environ.get(
            "MAIN_URL",
            f"http://127.0.0.1:{SERVICE_PORTS['main']}",
        ).rstrip("/")

        run_mode = os.environ.get("RUN_MODE") or "multi"
        if run_mode not in ("multi", "single"):
            raise SettingsError(f"Incorrect `RUN_MODE`: {run_mode!r}. Must be one of ['multi', 'single'].")

        # The address this service reports to main on registration. Locally it is
        # loopback; in Docker compose sets it to the service's DNS name; in one process
        # it is the service's prefix on main's own port.
        if run_mode == "single" and name != "main":
            service_self_url = f"{main_url}{SINGLE_PREFIX}/{name}"
        else:
            service_self_url = os.environ.get("SELF_URL") or f"http://127.0.0.1:{service_port}"

        # Empty token = the check is off; the launcher generates one for local runs.
        internal_token = os.environ.get("INTERNAL_TOKEN", "")
        api_token = os.environ.get("API_TOKEN", "")

        allowed_hosts = _list(os.environ.get("ALLOWED_HOSTS") or "127.0.0.1,localhost")
        allowed_origins = _list(os.environ.get("ALLOWED_ORIGINS", ""))

        # -----------------------------
        # STORAGE
        # -----------------------------

        data_dir = path_coercion(os.environ.get("DATA_DIR", "data"))
        log_dir = path_coercion(os.environ["LOG_DIR"]) if os.environ.get("LOG_DIR") else data_dir / "logs"

        # -----------------------------
        # SECRETS
        # -----------------------------

        keyring_backend = os.environ.get("KEYRING_BACKEND", "system").strip().lower()
        if keyring_backend not in ("system", "file"):
            raise SettingsError(
                f"Incorrect `KEYRING_BACKEND`: {keyring_backend!r}. Must be one of ['system', 'file']."
            )

        keyring_password = os.environ.get("KEYRING_PASSWORD", "")


        return cls(
            service=ServiceSettings(
                name=name,
                host=service_host,
                port=service_port,
                self_url=service_self_url,
                main_url=main_url,
                internal_token=internal_token,
                api_token=api_token,
                allowed_hosts=allowed_hosts,
                allowed_origins=allowed_origins,
                run_mode=run_mode,
            ),
            storage=StorageSettings(
                data_dir=data_dir,
                log_dir=log_dir,
            ),
            llm=_llm_from_env(),
            secrets=SecretsSettings(
                keyring_backend=keyring_backend,
                keyring_password=keyring_password,
            ),
        )


def _llm_from_env() -> LLMSettings | None:
    """Models named by LLM_* variables, when LLM_MODEL is set. They are not used
    directly: main registers them as providers on start (main/providers.py), and
    roles point at providers. Timeouts, retries, proxy and reasoning effort are
    settings of their own (common/tunables.py)."""
    llm_model = os.environ.get("LLM_MODEL")
    if not llm_model:
        return None

    base_url = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")

    embed_base_url = (os.environ.get("LLM_EMBED_BASE_URL") or base_url).rstrip("/")

    # Empty means unset, as for every other variable here (the .env templates ship it
    # empty): the chat server's key then serves embeddings on that same server, and a
    # separate embeddings server never inherits it.
    embed_api_key = os.environ.get("LLM_EMBED_API_KEY") or (api_key if embed_base_url == base_url else "")

    return LLMSettings(
        base_url=base_url,
        api_key=api_key,
        # One model serves every role unless a role names its own.
        model_chat=os.environ.get("LLM_MODEL_CHAT") or llm_model,
        model_instruct=os.environ.get("LLM_MODEL_INSTRUCT") or llm_model,
        model_embed=os.environ.get("LLM_MODEL_EMBED", ""),
        embed_base_url=embed_base_url,
        embed_api_key=embed_api_key,
    )


def _list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a `.env` file into a dict; `#` starts a comment, quotes are stripped."""
    values: dict[str, str] = {}

    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, raw = line.split("=", 1)
        value = raw.strip()

        # A quoted value is taken whole (a password may hold " #"); a comment may follow it.
        # Otherwise a comment starts at " #" in the line as written: `KEY= # note` is empty.
        if value[:1] in ("'", '"') and value[0] in value[1:]:
            value = value[1 : value.index(value[0], 1)]
        else:
            value = raw.split(" #", 1)[0].strip()

        values[key.strip()] = value

    return values


def path_coercion(value: str) -> Path:
    """Resolve a path; relative paths are anchored at the repository root."""
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = ROOT_DIR / path

    return path.resolve()


def int_coercion(
    value: str,
    name: str,
    low: int | None = None,
    high: int | None = None,
) -> int:
    """Convert a string to an integer and validate its bounds."""
    try:
        coerced_value = int(value)
    except ValueError as e:
        raise SettingsError(f"`{name}` must be an integer.") from e

    if low is not None and coerced_value < low:
        raise SettingsError(
            f"`{name}` must be greater than or equal to {low}. "
            f"Got {coerced_value}."
        )

    if high is not None and coerced_value > high:
        raise SettingsError(
            f"`{name}` must be less than or equal to {high}. "
            f"Got {coerced_value}."
        )

    return coerced_value
