"""An external tool version as the executor's registry holds it: the same face as an
internal ToolSpec (parse, call, manifest), a different way to run."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from common import tunables
from common.external_tools import deps, process, store
from common.external_tools.manifest import Manifest
from common.settings import StorageSettings
from common.storage.models import ExternalToolVersion
from common.tools import ToolContext, ToolError, function_name
from common.tools.context import PermissionDenied


@dataclass(slots=True, frozen=True)
class ExternalToolSpec:
    name: str
    title: str
    card: str
    description: str
    confirm: bool | str
    timeout: int | None
    path_args: dict[str, str]
    manifest_data: dict[str, Any]
    version: int
    # The version's files must still match it when it runs.
    hash: str
    folder: Path
    storage: StorageSettings
    secrets: tuple[str, ...] = ()
    core: bool = False
    _validator: Any = field(default=None, compare=False)

    @classmethod
    def of(cls, row: ExternalToolVersion, storage: StorageSettings) -> ExternalToolSpec:
        manifest = Manifest.model_validate(row.manifest)

        return cls(
            name=row.name,
            title=manifest.title,
            card=manifest.card or manifest.title,
            description=manifest.description,
            confirm=manifest.confirm,
            timeout=manifest.timeout,
            path_args=manifest.path_args(),
            manifest_data=manifest.model_dump(),
            version=row.version,
            hash=row.hash,
            folder=store.version_dir(storage, row.name, row.version),
            storage=storage,
            _validator=jsonschema.Draft202012Validator(manifest.input_schema),
        )


    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "function_name": function_name(self.name),
            "title": self.title,
            "card": self.card,
            "description": self.description,
            "confirm": self.confirm,
            "secrets": [],
            "timeout": self.timeout,
            "input_schema": self.manifest_data["input_schema"],
            "output_schema": self.manifest_data["output_schema"],
            "settings_schema": None,
            "guide": None,
            "core": False,
            "source": "external",
            "version": self.version,
        }


    def parse(self, args: dict[str, Any]) -> dict[str, Any]:
        errors = sorted(self._validator.iter_errors(args), key=lambda e: list(e.path))
        if errors:
            raise ToolError("; ".join(f"{'.'.join(map(str, e.path)) or 'аргументы'}: {e.message}" for e in errors[:5]))

        return dict(args)


    async def prepare(self) -> None:
        """Its packages, installed on the first call (up to uv's own time limit)."""
        await self._checked_python()


    async def call(self, inp: dict[str, Any], ctx: ToolContext) -> Any:
        python = await self._checked_python()

        request = {
            "args": {key: self._full_paths(key, value, ctx) for key, value in inp.items()},
            "context": {
                "mode": ctx.mode,
                "dry_run": ctx.dry_run,
                "output_dir": str(ctx.output_dir()),
                "session_id": ctx.session_id,
                "run_id": ctx.run_id,
                "step_key": ctx.step_key,
            },
        }

        timeout = self.timeout or tunables.get("tool_timeout_sec")
        answer = await asyncio.to_thread(process.run, python, self.folder, request, timeout)

        for line in answer.get("logs") or []:
            ctx.log(str(line))

        if answer.get("printed"):
            ctx.log(answer["printed"])

        if not answer.get("ok"):
            if answer.get("traceback"):
                ctx.log(answer["traceback"])

            raise ToolError(answer.get("error") or "Команда завершилась без ответа")

        for path in answer.get("artifacts") or []:
            ctx.artifacts.append(_ref(Path(path), ctx) or path)

        output = _refs_back(answer.get("output"), ctx)

        try:
            jsonschema.validate(output, self.manifest_data["output_schema"])

        except jsonschema.ValidationError as e:
            raise ToolError(f"Ответ `{self.name}` не по его схеме: {e.message}") from e

        return output


    async def _checked_python(self) -> Path:
        if await asyncio.to_thread(store.files_hash, self.folder) != self.hash:
            raise ToolError(f"Файлы `{self.name}` v{self.version} изменились после одобрения: команда не запущена")

        return await asyncio.to_thread(deps.ensure_venv, self.folder, self.storage.uv_cache_dir)


    def _full_paths(self, key: str, value: Any, ctx: ToolContext) -> Any:
        """Workspace refs become full paths: the tool works with files, not our refs."""
        kind = self.path_args.get(key)
        if kind == "path" and isinstance(value, str):
            return str(ctx.read_path(value))

        if kind == "refs" and isinstance(value, list):
            return [str(ctx.read_path(item)) if isinstance(item, str) else item for item in value]

        return value


def _refs_back(value: Any, ctx: ToolContext) -> Any:
    """Full paths in the answer back to refs (`Отчёты/x.xlsx`, `data:outputs/…`)."""
    if isinstance(value, dict):
        return {key: _refs_back(item, ctx) for key, item in value.items()}

    if isinstance(value, list):
        return [_refs_back(item, ctx) for item in value]

    if isinstance(value, str) and len(value) < 1000 and Path(value).is_absolute():
        return _ref(Path(value), ctx) or value

    return value


def _ref(path: Path, ctx: ToolContext) -> str | None:
    try:
        return ctx.to_ref(path)

    except (PermissionDenied, ValueError, OSError):
        return None
