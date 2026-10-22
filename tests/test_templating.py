"""Task templates: date filters, native values for single expressions, references."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from jinja2 import TemplateError

from common import templating


FRIDAY = date(2026, 10, 16)


def test_week_filters():
    assert templating.start_of_prev_week(FRIDAY) == date(2026, 10, 5)
    assert templating.end_of_prev_week(FRIDAY) == date(2026, 10, 11)
    assert templating.start_of_week(FRIDAY) == date(2026, 10, 12)


def test_month_filters():
    assert templating.start_of_prev_month(FRIDAY) == date(2026, 9, 1)
    assert templating.end_of_prev_month(FRIDAY) == date(2026, 9, 30)
    assert templating.end_of_month(date(2026, 2, 10)) == date(2026, 2, 28)


def test_single_expression_keeps_native_value():
    ctx = {"files": {"output": {"files": [{"path": "a.xlsx"}]}}}

    assert templating.render_str("{{ files.output.files }}", ctx) == [{"path": "a.xlsx"}]


def test_mixed_text_renders_string():
    ctx = {"today": FRIDAY}

    assert templating.render_str("Отчёт {{ today | start_of_prev_week | format_date }}", ctx) == "Отчёт 05.10.2026"


def test_format_date_keeps_the_time_of_a_moment():
    """`now | format_date('%H:%M')` gave 00:00: the filter cut every value to a date."""
    ctx = {"now": datetime(2026, 10, 22, 12, 12, 5), "today": FRIDAY}

    assert templating.render_str("{{ now | format_date('%Y-%m-%dT%H-%M-%S') }}", ctx) == "2026-10-22T12-12-05"
    assert templating.render_str("{{ today | format_date }}", ctx) == "16.10.2026"


def test_render_nested_and_jsonable():
    ctx = {"today": FRIDAY}
    value = templating.render({"period": ["{{ today | start_of_prev_week }}", "{{ today | end_of_prev_week }}"]}, ctx)

    assert templating.jsonable(value) == {"period": ["2026-10-05", "2026-10-11"]}


def test_undefined_names_fail():
    with pytest.raises(TemplateError):
        templating.render_str("{{ missing.output }}", {})


def test_referenced_names():
    assert templating.referenced_names({"a": "{{ files.output }} and {{ week_start }}"}) == {"files", "week_start"}


def test_sandbox_blocks_attribute_escape():
    with pytest.raises(TemplateError):
        templating.render_str("{{ ''.__class__.__mro__ }}", {})
