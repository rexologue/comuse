"""Deterministic tools that frozen tasks rely on instead of the LLM."""

from __future__ import annotations

import pytest

from common.tools import ToolError, discover
from internal_tools.data.map_rows import Input as MapInput, map_rows


GROUPS = [
    {"key": "АО Техноресурс", "count": 5, "sums": {"Сумма": 334500.0}},
    {"key": "ООО Ромашка", "count": 4, "sums": {"Сумма": 289000.0}},
    {"key": "ИП Смирнов", "count": 4, "sums": {"Сумма": 229000.0}},
]


async def test_map_rows_renames_formats_and_limits():
    out = await map_rows(
        MapInput(rows=GROUPS, fields={"client": "row.key", "amount": "row.sums['Сумма'] | format_number"}, limit=2),
        ctx=None,
    )

    assert out.rows == [
        {"client": "АО Техноресурс", "amount": "334 500"},
        {"client": "ООО Ромашка", "amount": "289 000"},
    ]


async def test_map_rows_reports_bad_expression():
    with pytest.raises(ToolError):
        await map_rows(MapInput(rows=GROUPS, fields={"x": "row.nope.deeper"}), ctx=None)


def test_catalog_has_the_deterministic_tools():
    names = set(discover())

    assert {"xlsx.aggregate", "data.map_rows", "docs.fill_template"} <= names


def _template(path):
    from docx import Document

    document = Document()
    document.add_paragraph("Итого: {{ total }}")
    table = document.add_table(rows=4, cols=1)
    table.rows[0].cells[0].text = "{%tr for row in items %}"
    table.rows[1].cells[0].text = "{{ row.name }}"
    table.rows[2].cells[0].text = "{%tr endfor %}"
    table.rows[3].cells[0].text = "конец"
    document.save(str(path))

    return path


def test_fill_template_checks_fields_and_row_keys(tmp_path):
    from docx import Document

    from internal_tools.docs.fill_template import _render

    template = _template(tmp_path / "t.docx")
    out = tmp_path / "out.docx"

    with pytest.raises(ToolError, match="total"):
        _render(template, {"items": []}, {}, out)

    with pytest.raises(ToolError, match="data.map_rows"):
        _render(template, {"total": 1, "items": [{"key": "a"}]}, {}, out)

    _render(template, {"total": "3", "items": [{"name": "Ромашка"}]}, {}, out)
    text = [p.text for p in Document(str(out)).paragraphs] + [c.text for t in Document(str(out)).tables for r in t.rows for c in r.cells]

    assert "Итого: 3" in text and "Ромашка" in text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a/b.xlsx", ["a/b.xlsx"]),
        ('["a/b.xlsx", "c/d.xlsx"]', ["a/b.xlsx", "c/d.xlsx"]),
        ("a/b.xlsx, c/d.xlsx", ["a/b.xlsx", "c/d.xlsx"]),
        ({"files": [{"path": "a.xlsx", "name": "a.xlsx"}]}, ["a.xlsx"]),
        (["a.xlsx", {"path": "b.xlsx"}], ["a.xlsx", "b.xlsx"]),
        ("Отчёты/Иванов, Петров.docx", ["Отчёты/Иванов, Петров.docx"]),
    ],
)
def test_file_refs_accepts_every_shape(value, expected):
    from common.tools.refs import file_refs

    assert file_refs(value) == expected


def test_aggregate_inputs_have_a_schema():
    schema = discover()["xlsx.aggregate"].manifest()["input_schema"]["properties"]["inputs"]

    assert "anyOf" in schema


async def test_move_in_a_dry_run_only_says_what_it_would_do(settings):
    from common.tools import ToolContext
    from executor.paths import WorkspacePaths
    from internal_tools.fs.transfer import Input, move_file

    workspace = settings.storage.workspace_dir
    (workspace / "Входящие").mkdir()
    (workspace / "Входящие" / "a.pdf").write_bytes(b"%PDF")

    ctx = ToolContext(
        mode="run", settings=settings, paths=WorkspacePaths(workspace, settings.storage.data_dir), llm=None, dry_run=True,
    )
    out = await move_file(Input(src="Входящие/a.pdf", dst="Счета/"), ctx)

    assert out.path == "Счета/a.pdf"
    assert (workspace / "Входящие" / "a.pdf").exists() and not (workspace / "Счета").exists()
    assert ctx.logs == ["Пробный запуск: переложил бы «Входящие/a.pdf» в «Счета/a.pdf»"]
