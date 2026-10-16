"""What a tool receives besides its input: paths, accounts, LLM and the call mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol

if TYPE_CHECKING:
    from common.clients.llm_client import LLMClient
    from common.schemas.task import Permissions
    from common.settings import Settings


class ToolError(Exception):
    """Expected tool failure; the message is shown to the user and the LLM."""


class PermissionDenied(ToolError):
    """The call reaches outside what the task (or chat) is allowed to touch."""


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
class MailAccount:
    name: str
    config: dict[str, Any]
    secret: str


@dataclass(slots=True)
class ToolContext:
    mode: Literal["chat", "run"]
    settings: Settings
    paths: PathResolver
    llm: LLMClient
    permissions: Permissions | None
    get_account: Callable[[str], MailAccount]
    publish: Callable[[str, dict[str, Any]], Awaitable[None]]
    session_id: str | None = None
    run_id: str | None = None
    step_key: str | None = None
    dry_run: bool = False
    # External actions create drafts instead of real effects (dry run, first runs).
    draft: bool = False
    artifacts: list[str] = field(default_factory=list)

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


    def account(self, name: str) -> MailAccount:
        """Mail account by name; a task may only use the accounts it declares."""
        if self.permissions is not None and name not in self.permissions.mail_accounts:
            raise PermissionDenied(f"Аккаунт `{name}` не разрешён этой задаче")

        return self.get_account(name)


    def check_recipients(self, recipients: list[str]) -> None:
        if self.permissions is None:
            return

        allowed = {r.lower() for r in self.permissions.mail_send_to}
        denied = [r for r in recipients if r.lower() not in allowed]

        if denied:
            raise PermissionDenied(f"Адресаты не разрешены этой задаче: {', '.join(denied)}")
