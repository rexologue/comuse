"""Shared settings for every service, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class SettingsError(Exception):
    """Raised when service configuration is invalid."""


ROOT_DIR = Path(__file__).resolve().parent.parent

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
    api_token: str


@dataclass(slots=True, frozen=True)
class StorageSettings:
    data_dir: Path
    workspace_dir: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"


    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"


    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"


@dataclass(slots=True, frozen=True)
class LLMSettings:
    base_url: str
    api_key: str
    no_proxy: bool
    model_chat: str
    model_instruct: str
    # Empty = no embeddings: the agent sees every tool instead of the top-k.
    model_embed: str
    chat_reasoning_effort: str
    timeout: int
    max_retries: int


@dataclass(slots=True, frozen=True)
class AgentSettings:
    max_iterations: int
    tools_top_k: int
    history_messages: int


@dataclass(slots=True, frozen=True)
class CompilerSettings:
    max_attempts: int
    dry_run_timeout: int


@dataclass(slots=True, frozen=True)
class ExecutorSettings:
    tool_timeout: int
    keyring_backend: str
    keyring_password: str


@dataclass(slots=True, frozen=True)
class SchedulerSettings:
    watch_polling: bool
    mail_poll_minutes: int


@dataclass(slots=True, frozen=True)
class Settings:
    service: ServiceSettings
    storage: StorageSettings
    llm: LLMSettings
    agent: AgentSettings
    compiler: CompilerSettings
    executor: ExecutorSettings
    scheduler: SchedulerSettings

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

        # The address this service reports to main on registration. Locally it is
        # loopback; in Docker compose sets it to the service's DNS name.
        service_self_url = os.environ.get("SELF_URL") or f"http://127.0.0.1:{service_port}"

        main_url = os.environ.get(
            "MAIN_URL",
            f"http://127.0.0.1:{SERVICE_PORTS['main']}",
        ).rstrip("/")

        # Empty token = the check is off; the launcher generates one for local runs.
        internal_token = os.environ.get("INTERNAL_TOKEN", "")
        api_token = os.environ.get("API_TOKEN", "")

        # -----------------------------
        # STORAGE
        # -----------------------------

        data_dir = path_coercion(os.environ.get("DATA_DIR", "data"))
        workspace_dir = path_coercion(os.environ.get("WORKSPACE_DIR", "workspace"))

        # -----------------------------
        # LLM (OpenAI-compatible)
        # -----------------------------

        llm_base_url = os.environ.get(
            "LLM_BASE_URL",
            "http://127.0.0.1:11434/v1",
        ).rstrip("/")

        llm_api_key = os.environ.get("LLM_API_KEY", "")

        llm_no_proxy = bool_coercion(
            os.environ.get("LLM_NO_PROXY", "true"),
            "LLM_NO_PROXY",
        )

        # One model serves everything unless a role overrides it: CHAT drives the
        # multi-step agent loop, INSTRUCT does single structured calls (llm.* tools,
        # template markup, the compiler).
        llm_model = require_env("LLM_MODEL")
        llm_model_chat = os.environ.get("LLM_MODEL_CHAT") or llm_model
        llm_model_instruct = os.environ.get("LLM_MODEL_INSTRUCT") or llm_model
        llm_model_embed = os.environ.get("LLM_MODEL_EMBED", "")

        # low | medium | high for reasoning models; empty = the field is not sent.
        llm_chat_reasoning_effort = os.environ.get("LLM_CHAT_REASONING_EFFORT", "").strip().lower()
        if llm_chat_reasoning_effort not in ("", "low", "medium", "high"):
            raise SettingsError(
                f"Incorrect `LLM_CHAT_REASONING_EFFORT`: {llm_chat_reasoning_effort!r}. "
                f"Must be empty or one of ['low', 'medium', 'high']."
            )

        llm_timeout = int_coercion(
            os.environ.get("LLM_TIMEOUT_SEC", "300"),
            "LLM_TIMEOUT_SEC",
            low=1,
        )

        llm_max_retries = int_coercion(
            os.environ.get("LLM_MAX_RETRIES", "2"),
            "LLM_MAX_RETRIES",
            low=0,
        )

        # -----------------------------
        # AGENT LOOP
        # -----------------------------

        agent_max_iterations = int_coercion(
            os.environ.get("AGENT_MAX_ITERATIONS", "10"),
            "AGENT_MAX_ITERATIONS",
            low=1,
        )

        agent_tools_top_k = int_coercion(
            os.environ.get("AGENT_TOOLS_TOP_K", "24"),
            "AGENT_TOOLS_TOP_K",
            low=1,
        )

        agent_history_messages = int_coercion(
            os.environ.get("AGENT_HISTORY_MESSAGES", "40"),
            "AGENT_HISTORY_MESSAGES",
            low=2,
        )

        # -----------------------------
        # COMPILER
        # -----------------------------

        compiler_max_attempts = int_coercion(
            os.environ.get("COMPILER_MAX_ATTEMPTS", "3"),
            "COMPILER_MAX_ATTEMPTS",
            low=1,
        )

        compiler_dry_run_timeout = int_coercion(
            os.environ.get("COMPILER_DRY_RUN_TIMEOUT_SEC", "300"),
            "COMPILER_DRY_RUN_TIMEOUT_SEC",
            low=1,
        )

        # -----------------------------
        # EXECUTOR
        # -----------------------------

        executor_tool_timeout = int_coercion(
            os.environ.get("TOOL_TIMEOUT_SEC", "300"),
            "TOOL_TIMEOUT_SEC",
            low=1,
        )

        keyring_backend = os.environ.get("KEYRING_BACKEND", "system").strip().lower()
        if keyring_backend not in ("system", "file"):
            raise SettingsError(
                f"Incorrect `KEYRING_BACKEND`: {keyring_backend!r}. Must be one of ['system', 'file']."
            )

        keyring_password = os.environ.get("KEYRING_PASSWORD", "")

        # -----------------------------
        # SCHEDULER
        # -----------------------------

        # Docker Desktop does not forward inotify events from Windows bind mounts,
        # so containers fall back to polling the watched folders.
        watch_polling = bool_coercion(
            os.environ.get("WATCH_POLLING", "false"),
            "WATCH_POLLING",
        )

        mail_poll_minutes = int_coercion(
            os.environ.get("MAIL_POLL_MINUTES", "5"),
            "MAIL_POLL_MINUTES",
            low=1,
        )

        return cls(
            service=ServiceSettings(
                name=name,
                host=service_host,
                port=service_port,
                self_url=service_self_url,
                main_url=main_url,
                internal_token=internal_token,
                api_token=api_token,
            ),
            storage=StorageSettings(
                data_dir=data_dir,
                workspace_dir=workspace_dir,
            ),
            llm=LLMSettings(
                base_url=llm_base_url,
                api_key=llm_api_key,
                no_proxy=llm_no_proxy,
                model_chat=llm_model_chat,
                model_instruct=llm_model_instruct,
                model_embed=llm_model_embed,
                chat_reasoning_effort=llm_chat_reasoning_effort,
                timeout=llm_timeout,
                max_retries=llm_max_retries,
            ),
            agent=AgentSettings(
                max_iterations=agent_max_iterations,
                tools_top_k=agent_tools_top_k,
                history_messages=agent_history_messages,
            ),
            compiler=CompilerSettings(
                max_attempts=compiler_max_attempts,
                dry_run_timeout=compiler_dry_run_timeout,
            ),
            executor=ExecutorSettings(
                tool_timeout=executor_tool_timeout,
                keyring_backend=keyring_backend,
                keyring_password=keyring_password,
            ),
            scheduler=SchedulerSettings(
                watch_polling=watch_polling,
                mail_poll_minutes=mail_poll_minutes,
            ),
        )


def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a `.env` file into a dict; `#` starts a comment, quotes are stripped."""
    values: dict[str, str] = {}

    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        values[key.strip()] = value

    return values


def path_coercion(value: str) -> Path:
    """Resolve a path; relative paths are anchored at the repository root."""
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = ROOT_DIR / path

    return path.resolve()


def require_env(name: str) -> str:
    """Return the value of a required environment variable."""
    value = os.environ.get(name)

    if value is None:
        raise SettingsError(f"`{name}` environment variable is required.")

    return value


def bool_coercion(value: str, name: str) -> bool:
    """Convert a string to a boolean, accepting the usual truthy/falsy spellings."""
    normalized = value.strip().lower()

    if normalized in ("1", "true", "yes", "on"):
        return True

    if normalized in ("0", "false", "no", "off"):
        return False

    raise SettingsError(f"`{name}` must be a boolean (got {value!r}).")


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
