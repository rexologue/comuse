"""Task paths → real files. Tasks only ever hold portable references:

- `Отчёты/Продажи/q3.xlsx` — relative to WORKSPACE_DIR (Windows folder or /workspace);
- `data:templates/weekly.docx`, `data:outputs/…` — inside DATA_DIR.

Every resolution normalizes the path, refuses escapes (`..`, symlinks out of the root)
and checks the task's folder permissions."""

from __future__ import annotations

from pathlib import Path

from common.pathrefs import DATA_PREFIX, normalize_folder
from common.schemas.task import Permissions
from common.tools import PermissionDenied


# Only these DATA_DIR subfolders are visible to tools (app.db and secrets are not).
_DATA_AREAS = ("templates", "outputs")


class WorkspacePaths:
    def __init__(
        self,
        workspace_dir: Path,
        data_dir: Path,
        permissions: Permissions | None,
    ):

        self.workspace_dir = workspace_dir.resolve()
        self.data_dir      = data_dir.resolve()
        self.permissions   = permissions


    def read_path(self, ref: str) -> Path:
        path, in_data = self._resolve(ref)

        if not in_data and self.permissions is not None:
            folders = self.permissions.read_folders + self.permissions.write_folders
            self._check_folder(path, folders, "чтение")

        return path


    def write_path(self, ref: str) -> Path:
        path, in_data = self._resolve(ref)

        if not in_data and self.permissions is not None:
            self._check_folder(path, self.permissions.write_folders, "запись")

        return path


    def to_ref(self, path: Path) -> str:
        resolved = path.resolve()

        if resolved.is_relative_to(self.data_dir):
            return DATA_PREFIX + resolved.relative_to(self.data_dir).as_posix()

        if resolved.is_relative_to(self.workspace_dir):
            return resolved.relative_to(self.workspace_dir).as_posix()

        raise PermissionDenied(f"Путь вне рабочей папки: {path}")


    def _resolve(self, ref: str) -> tuple[Path, bool]:
        ref = (ref or "").strip()

        if ref.startswith(DATA_PREFIX):
            rel = ref[len(DATA_PREFIX):].lstrip("/\\")
            path = (self.data_dir / rel).resolve()

            if not any(path.is_relative_to(self.data_dir / area) for area in _DATA_AREAS):
                raise PermissionDenied(f"Доступ к `{ref}` запрещён: разрешены только data:templates и data:outputs")

            return path, True

        candidate = Path(ref.replace("\\", "/")).expanduser()

        # An absolute path is accepted only when it already points into the workspace.
        path = candidate.resolve() if candidate.is_absolute() else (self.workspace_dir / candidate).resolve()

        if not path.is_relative_to(self.workspace_dir):
            raise PermissionDenied(f"Путь `{ref}` выходит за пределы рабочей папки")

        return path, False


    def _check_folder(self, path: Path, folders: list[str], action: str) -> None:
        rel = path.relative_to(self.workspace_dir).as_posix()

        for folder in folders:
            normalized = normalize_folder(folder)
            if normalized == "" or rel == normalized or rel.startswith(normalized + "/"):
                return

        raise PermissionDenied(
            f"{action.capitalize()} `{rel}` не разрешено этой задаче "
            f"(разрешены: {', '.join(folders) or 'ничего'})"
        )

