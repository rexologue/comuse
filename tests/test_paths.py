"""Workspace sandbox: references resolve inside their roots, escapes are refused, and new
files get names Windows accepts (the workspace is often a Windows folder)."""

from __future__ import annotations

import pytest

from common.tools import PermissionDenied, ToolError
from executor.paths import WorkspacePaths


@pytest.fixture
def roots(tmp_path):
    workspace, data = tmp_path / "ws", tmp_path / "data"
    (workspace / "Отчёты" / "Продажи").mkdir(parents=True)
    (data / "templates").mkdir(parents=True)

    return workspace, data


def test_relative_and_data_refs(roots):
    workspace, data = roots
    paths = WorkspacePaths(workspace, data)

    assert paths.read_path("Отчёты/Продажи") == (workspace / "Отчёты" / "Продажи").resolve()
    assert paths.read_path("data:templates/t.docx") == (data / "templates" / "t.docx").resolve()
    assert paths.to_ref(data / "templates" / "t.docx") == "data:templates/t.docx"
    assert paths.to_ref(workspace / "Отчёты" / "a.txt") == "Отчёты/a.txt"


@pytest.mark.parametrize("ref", ["../secret.txt", "Отчёты/../../x", "/etc/passwd", "data:app.db", "data:../x"])
def test_escapes_are_refused(roots, ref):
    workspace, data = roots

    with pytest.raises(PermissionDenied):
        WorkspacePaths(workspace, data).read_path(ref)


def test_symlink_out_of_workspace_is_refused(roots, tmp_path):
    workspace, data = roots
    (tmp_path / "outside").mkdir()
    (workspace / "link").symlink_to(tmp_path / "outside")

    with pytest.raises(PermissionDenied):
        WorkspacePaths(workspace, data).read_path("link/file.txt")


@pytest.fixture
def sandbox(tmp_path) -> WorkspacePaths:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "data" / "outputs").mkdir(parents=True)

    return WorkspacePaths(tmp_path / "workspace", tmp_path / "data")


def test_a_new_name_windows_refuses_is_refused_with_a_way_out(sandbox):
    with pytest.raises(ToolError, match="«2026-10-22T12:00:00.txt».*«:».*«2026-10-22T12-00-00.txt»"):
        sandbox.write_path("Стихи/2026-10-22T12:00:00.txt")


@pytest.mark.parametrize("name", ["CON.txt", "отчёт?.docx", "черновик."])
def test_other_names_windows_refuses(sandbox, name):
    with pytest.raises(ToolError, match="Windows"):
        sandbox.write_path(f"Отчёты/{name}")


def test_what_already_exists_is_not_judged(sandbox):
    """A file made elsewhere (on Linux) can still be moved away or overwritten."""
    existing = sandbox.workspace_dir / "старое: имя.txt"
    existing.write_text("x")

    assert sandbox.write_path("старое: имя.txt") == existing


def test_portable_names_and_data_refs_pass(sandbox):
    assert sandbox.write_path("Стихи/2026-10-22T12-00-00.txt").name == "2026-10-22T12-00-00.txt"
    assert sandbox.write_path("data:outputs/run_1/report.docx").name == "report.docx"


def test_a_data_dir_inside_the_workspace_stays_closed(tmp_path):
    # The repository (and DATA_DIR=data in it) inside the user's Documents.
    workspace = tmp_path / "Documents"
    data = workspace / "agent" / "data"
    (data / "outputs").mkdir(parents=True)
    (data / "app.db").write_text("")
    paths = WorkspacePaths(workspace, data)

    for ref in ("agent/data/app.db", "agent/data/secrets.enc", str(data / "app.db")):
        with pytest.raises(PermissionDenied):
            paths.read_path(ref)

    assert paths.to_ref(data / "outputs" / "a.txt") == "data:outputs/a.txt"
    with pytest.raises(PermissionDenied):
        paths.to_ref(data / "app.db")


def test_a_workspace_inside_the_data_dir_keeps_its_refs(tmp_path):
    data = tmp_path / "data"
    workspace = data / "workspace"
    workspace.mkdir(parents=True)
    paths = WorkspacePaths(workspace, data)

    ref = paths.to_ref(workspace / "a.txt")
    assert ref == "a.txt" and paths.read_path(ref) == (workspace / "a.txt").resolve()
