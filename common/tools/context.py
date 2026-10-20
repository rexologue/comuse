"""What a tool receives besides its input: paths, accounts, LLM and the call mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from common.clients.llm_client import LLMClient
    from common.settings import Settings
    from common.tools.domain import DomainSettings


SettingsT = TypeVar("SettingsT", bound="DomainSettings")


class ToolError(Exception):
    """Expected tool failure; the message is shown to the user and the LLM."""


class PermissionDenied(ToolError):
    """The call reaches outside the user's workspace (or a tool's own limits)."""


class NeedsInput(Exception):
    """The tool needs an answer from the user before it can produce output."""

    def __init__(self, question: str, options: list[str] | None = None) -> None:
        super().__init__(question)

        self.question = question
        self.options = options or []


class PathResolver(Protocol):
    def read_path(self, ref: str) -> Path: ...
    def write_path(self, ref: str) -> Path: ...
    def to_ref(self, path: Path) -> str: ...


@dataclass(slots=True)
class Credential:
    """A stored account: non-secret settings plus the password from the keyring."""

    name: str
    kind: str
    config: dict[str, Any]
    secret: str


@dataclass(slots=True)
class ToolContext:
    mode: Literal["chat", "run"]
    settings: Settings
    paths: PathResolver
    llm: LLMClient
    publish: Callable[[str, dict[str, Any]], Awaitable[None]]
    session_id: str | None = None
    run_id: str | None = None
    step_key: str | None = None
    dry_run: bool = False
    # Dry run: external actions create drafts instead of real effects.
    draft: bool = False
    # The task's domain settings sections ({"mail": {...}}); empty in chat.
    task_settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Accounts the executor loaded for the tool's `secrets` arguments, by argument name.
    credentials: dict[str, Credential] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)

    def domain_settings(self, model: type[SettingsT]) -> SettingsT:
        """The task's values for this domain; defaults in chat and for anything unset."""
        return model.model_validate(self.task_settings.get(model.domain, {}))


    def read_path(self, ref: str) -> Path:
        return self.paths.read_path(ref)


    def write_path(self, ref: str) -> Path:
        return self.paths.write_path(ref)


    def to_ref(self, path: Path) -> str:
        return self.paths.to_ref(path)


    def output_dir(self, subdir: str = "") -> Path:
        """`data:outputs/<run or session>/<subdir>`, created on demand."""
        scope = self.run_id or self.session_id or "adhoc"

        path = self.settings.storage.outputs_dir / scope
        if subdir:
            path = path / Path(subdir).name

        path.mkdir(parents=True, exist_ok=True)

        return path


    def output_path(self, filename: str) -> Path:
        """A file under `data:outputs/<run or session>/`, recorded as an artifact."""
        path = self.output_dir() / (Path(filename).name or "output")

        self.artifacts.append(self.to_ref(path))

        return path


    def credential(self, argument: str) -> Credential:
        if argument not in self.credentials:
            raise ToolError(f"Аргумент `{argument}` не объявлен в secrets команды")

        return self.credentials[argument]
