"""Task paths → real files. Tasks only ever hold portable references:

- `Отчёты/Продажи/q3.xlsx` — relative to WORKSPACE_DIR (Windows folder or /workspace);
- `data:templates/weekly.docx`, `data:outputs/…` — inside DATA_DIR.

Every resolution normalizes the path and refuses escapes (`..`, symlinks out of the
root, DATA_DIR beyond templates and outputs). What a task may touch is its approved
steps; this only keeps every path inside the user's workspace. A file or folder about
to be created must have a name Windows accepts (no `:`, `?`, `*`…)."""

from __future__ import annotations

from pathlib import Path

from common.pathrefs import DATA_PREFIX
from common.tools import PermissionDenied, ToolError


# Only these DATA_DIR subfolders are visible to tools (app.db and secrets are not).
_DATA_AREAS = ("templates", "outputs")


class WorkspacePaths:
    def __init__(self, workspace_dir: Path, data_dir: Path):

        self.workspace_dir = workspace_dir.resolve()
        self.data_dir      = data_dir.resolve()


    def read_path(self, ref: str) -> Path:
        return self._resolve(ref)


    def write_path(self, ref: str) -> Path:
        """A path to create or change. What does not exist yet must get a name Windows
        accepts too: the workspace is often a Windows folder or a network share."""
        path = self._resolve(ref)
        root = self.data_dir if path.is_relative_to(self.data_dir) else self.workspace_dir

        current = root
        for part in path.relative_to(root).parts:
            current = current / part
            if current.exists():
                continue

            if problem := unportable(part):
                raise ToolError(f"Имя «{part}» не подойдёт для Windows: {problem}. Например: «{portable(part)}»")

        return path


    def to_ref(self, path: Path) -> str:
        resolved = path.resolve()

        if resolved.is_relative_to(self.data_dir):
            return DATA_PREFIX + resolved.relative_to(self.data_dir).as_posix()

        if resolved.is_relative_to(self.workspace_dir):
            return resolved.relative_to(self.workspace_dir).as_posix()

        raise PermissionDenied(f"Путь вне рабочей папки: {path}")


    def _resolve(self, ref: str) -> Path:
        ref = (ref or "").strip()

        if ref.startswith(DATA_PREFIX):
            rel = ref[len(DATA_PREFIX):].lstrip("/\\")
            path = (self.data_dir / rel).resolve()

            if not any(path.is_relative_to(self.data_dir / area) for area in _DATA_AREAS):
                raise PermissionDenied(f"Доступ к `{ref}` запрещён: разрешены только data:templates и data:outputs")

            return path

        candidate = Path(ref.replace("\\", "/")).expanduser()

        # An absolute path is accepted only when it already points into the workspace.
        path = candidate.resolve() if candidate.is_absolute() else (self.workspace_dir / candidate).resolve()

        if not path.is_relative_to(self.workspace_dir):
            raise PermissionDenied(f"Путь `{ref}` выходит за пределы рабочей папки")

        return path


# What Windows refuses in a file or folder name.
_FORBIDDEN = set('<>:"|?*\\')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


def unportable(name: str) -> str | None:
    """Why Windows would refuse the name, or None."""
    bad = sorted({c for c in name if c in _FORBIDDEN or ord(c) < 32})
    if bad:
        return "в нём " + ", ".join(f"«{c}»" if ord(c) >= 32 else "управляющий символ" for c in bad)

    if name.endswith((" ", ".")):
        return "оно кончается пробелом или точкой"

    if name.split(".")[0].upper() in _RESERVED:
        return f"«{name.split('.')[0]}» — зарезервированное имя"

    return None


def portable(name: str) -> str:
    """The name as Windows takes it: `12:00:00` → `12-00-00`."""
    name = name.replace(":", "-")
    name = "".join("_" if c in _FORBIDDEN or ord(c) < 32 else c for c in name).rstrip(" .") or "_"

    return f"_{name}" if name.split(".")[0].upper() in _RESERVED else name
