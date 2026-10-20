"""Compiler static checks, permission derivation and step cards — against the real
tool catalog, so a renamed tool argument breaks these tests."""

from __future__ import annotations

import copy

import pytest

from common.schemas.task import TaskSpec
from common.tools import discover
from compiler.cards import build_cards, describe_trigger, diff_specs
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


def test_compiled_task_must_reflect_the_chat():
    from compiler.compile import check_reflects_chat

    notify_only = TaskSpec.model_validate({"title": "x", "steps": [{"id": "n", "tool": "user.notify", "args": {"message": "ok"}}]})

    with pytest.raises(SpecError):
        check_reflects_chat(notify_only, {"fs.list", "xlsx.aggregate", "user.notify"})

    check_reflects_chat(validate_spec(GOOD, CATALOG), {"fs.list", "docs.make_template"})


def test_mapping_cards_name_their_fields():
    spec = TaskSpec.model_validate({
        "title": "t",
        "steps": [{"id": "rows", "tool": "data.map_rows", "args": {"rows": [], "fields": {"client": "row.key", "amount": "row.sum"}, "limit": 5}}],
    })

    assert build_cards(spec, CATALOG)[0]["text"] == "Подготовить строки с полями client, amount, не больше 5"


AUTO_REPLY = {
    "title": "Ответ на заявки",
    "trigger": {"type": "cron", "expr": "*/5 * * * *"},
    "steps": [
        {"id": "found", "tool": "mail.search", "args": {"account": "work", "subject": "Заявка", "unseen": True, "sender_in": ["*@company.local"]}, "on_empty": "skip"},
        {
            "id": "reply",
            "foreach": "{{ found.output.messages }}",
            "as": "msg",
            "steps": [
                {
                    "id": "send",
                    "tool": "mail.send",
                    "args": {"account": "work", "to": ["{{ msg.sender_email }}"], "subject": "Re: {{ msg.subject }}", "body": "Заявка принята."},
                }
            ],
        }
    ],
}


def test_sender_limit_shows_on_the_card_and_in_the_diff():
    unlimited = copy.deepcopy(AUTO_REPLY)
    del unlimited["steps"][0]["args"]["sender_in"]

    old, new = validate_spec(unlimited, CATALOG), validate_spec(AUTO_REPLY, CATALOG)
    card = build_cards(new, CATALOG)[0]["text"]

    assert "только от *@company.local" in card
    assert any("*@company.local" in line for line in diff_specs(old, new, CATALOG)["lines"])


def test_plain_auto_reply_needs_no_confirmation_step():
    validate_spec(AUTO_REPLY, CATALOG)


CHAT = ["Каждому отправителю ответь: «Здравствуйте! Ваша заявка принята, мы свяжемся с вами в течение дня.» с темой «Re: <тема>»"]


def _with_body(body):
    spec = copy.deepcopy(AUTO_REPLY)
    spec["steps"][1]["steps"][0]["args"]["body"] = body
    return validate_spec(spec, CATALOG)


def test_retyped_text_with_a_slip_is_rejected():
    from compiler.compile import check_verbatim

    with pytest.raises(SpecError, match="мы свяжемся с вами"):
        check_verbatim(_with_body("Здравствуйте! Ваша заявка принята, мы свяжемс с вами в течение дня."), CHAT)


@pytest.mark.parametrize("body", [
    "Здравствуйте! Ваша заявка принята, мы свяжемся с вами в течение дня.",  # copied
    "Спасибо, письмо получено. Ответим позже.",                               # a text of its own
])
def test_copied_or_own_texts_pass(body):
    from compiler.compile import check_verbatim

    check_verbatim(_with_body(body), CHAT)
