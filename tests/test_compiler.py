"""Compiler static checks, permission derivation and step cards — against the real
tool catalog, so a renamed tool argument breaks these tests."""

from __future__ import annotations

import copy

import pytest

from common.schemas.task import TaskSpec
from common.tools import discover
from compiler.cards import build_cards, describe_trigger, diff_specs
from compiler.permissions import derive_permissions
from compiler.validate import SpecError, validate_spec


CATALOG = {name: spec.manifest() for name, spec in discover().items()}

GOOD = {
    "title": "Еженедельный отчёт",
    "trigger": {"type": "cron", "expr": "0 10 * * MON"},
    "variables": {"week_start": "{{ today | start_of_prev_week }}", "week_end": "{{ today | end_of_prev_week }}"},
    "steps": [
        {
            "id": "files",
            "tool": "fs.list",
            "args": {"folder": "Отчёты/Продажи", "pattern": "*.xlsx", "modified_from": "{{ week_start }}", "modified_to": "{{ week_end }}"},
        },
        {
            "id": "totals",
            "tool": "xlsx.aggregate",
            "args": {"inputs": "{{ files.output.files }}", "sum_columns": ["Сумма"], "group_by": "Клиент", "top_n": 3},
        },
        {
            "id": "doc",
            "tool": "docs.fill_template",
            "args": {
                "template": "data:templates/weekly.docx",
                "context": {"revenue": "{{ totals.output.totals['Сумма'] }}", "top": "{{ totals.output.groups }}"},
                "output_name": "Отчёт.docx",
            },
        },
        {
            "id": "sort",
            "foreach": "{{ files.output.files }}",
            "as": "file",
            "steps": [{"id": "move", "tool": "fs.move", "args": {"src": "{{ file.path }}", "dst": "Архив/{{ file.name }}"}}],
        },
    ],
}


def _bad(mutate) -> list[str]:
    spec = copy.deepcopy(GOOD)
    mutate(spec)

    with pytest.raises(SpecError) as e:
        validate_spec(spec, CATALOG)

    return e.value.errors


def test_good_spec_passes():
    assert validate_spec(GOOD, CATALOG).title == "Еженедельный отчёт"


def test_unknown_tool():
    errors = _bad(lambda s: s["steps"][0].update(tool="fs.nope"))
    assert "fs.nope" in errors[0]


def test_unknown_and_missing_arguments():
    def mutate(spec):
        args = spec["steps"][2]["args"]
        del args["template"]
        args["colour"] = "red"

    errors = _bad(mutate)
    joined = " ".join(errors)
    assert "template" in joined and "colour" in joined


def test_reference_to_a_later_step():
    errors = _bad(lambda s: s["steps"][0]["args"].update(folder="{{ doc.output.path }}"))
    assert "doc" in errors[0]


def test_reference_to_a_missing_output_field():
    errors = _bad(lambda s: s["steps"][2]["args"]["context"].update(revenue="{{ totals.output.total_revenue }}"))
    assert "total_revenue" in errors[0] and "totals" in errors[0]


def test_bad_cron():
    errors = _bad(lambda s: s["trigger"].update(expr="every monday"))
    assert "cron" in errors[0]


def test_absolute_path():
    errors = _bad(lambda s: s["steps"][0]["args"].update(folder="C:\\Users\\me\\Отчёты"))
    assert "абсолютный" in errors[0]


def test_duplicate_step_ids():
    errors = _bad(lambda s: s["steps"][1].update(id="files"))
    assert "duplicate" in errors[0]


def test_derive_permissions():
    spec = validate_spec(GOOD, CATALOG)
    permissions = derive_permissions(spec, CATALOG)

    assert permissions.read_folders == ["Отчёты/Продажи"]
    # fs.move dst "Архив/{{ file.name }}" → the literal folder before the template.
    assert permissions.write_folders == ["Архив"]


def test_cards_and_trigger_text():
    spec = validate_spec(GOOD, CATALOG)
    cards = build_cards(spec, CATALOG)

    assert cards[0]["text"] == "Взять файлы «*.xlsx» из папки «Отчёты/Продажи»"
    assert cards[3]["children"][0]["tool"] == "fs.move"
    assert describe_trigger(spec.trigger) == "по понедельникам в 10:00"


def test_diff_lists_changes_in_words():
    old = validate_spec(GOOD, CATALOG)
    changed = copy.deepcopy(GOOD)
    changed["trigger"]["expr"] = "0 9 * * FRI"
    changed["steps"][0]["args"]["pattern"] = "*.csv"
    new = TaskSpec.model_validate(changed)

    lines = diff_specs(old, new, CATALOG)["lines"]

    assert any("пятницам в 09:00" in line for line in lines)
    assert any("*.csv" in line for line in lines)


def test_prompt_examples_stay_valid():
    from compiler.prompts import _EXAMPLE, _FOREACH_EXAMPLE

    validate_spec(_EXAMPLE, CATALOG)
    validate_spec({"title": "t", "steps": [{"id": "files", "tool": "fs.list", "args": {}}, _FOREACH_EXAMPLE]}, CATALOG)


def test_unknown_filter_is_a_validation_error():
    errors = _bad(lambda s: s["steps"][1]["args"].update(inputs="{{ files.output.files | from_json }}"))
    assert "from_json" in errors[0]


def test_unknown_filter_in_foreach():
    errors = _bad(lambda s: s["steps"][3].update(foreach="{{ files.output.files | nope }}"))
    assert "nope" in " ".join(errors)
