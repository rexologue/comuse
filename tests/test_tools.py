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
