"""Workspace sandbox: references resolve inside their roots, escapes are refused."""

from __future__ import annotations

import pytest

from common.tools import PermissionDenied
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
