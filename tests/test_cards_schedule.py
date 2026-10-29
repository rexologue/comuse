"""What a person reads before approving: every changed argument in a diff, whatever
the card shows; a schedule in words that say what it does."""

from __future__ import annotations

import pytest

from common.cron import describe_schedule
from common.schemas.task import TaskSpec
from compiler.cards import diff_specs


CATALOG = {"mail.send": {"name": "mail.send", "title": "Отправить письмо", "card": "Отправить письмо {to} с темой «{subject}»"}}


def _spec(**args):
    step = {"id": "send", "tool": "mail.send", "args": {"account": "work", "to": ["boss@company.ru"], "subject": "Итоги", "body": "b", **args}}
    return TaskSpec.model_validate({"title": "Итоги", "trigger": {"type": "manual"}, "steps": [step]})


def test_a_recipient_no_card_names_shows_in_the_diff():
    lines = diff_specs(_spec(), _spec(bcc=["audit@other.ru"]), CATALOG)["lines"]

    assert lines == ["~ Отправить письмо boss@company.ru с темой «Итоги»: bcc: — → audit@other.ru"]


@pytest.mark.parametrize(
    ("expr", "words"),
    [
        ("0 9 * * *", "каждый день в 09:00"),
        ("30 18 * * MON-FRI", "по будням в 18:30"),
        ("0 9 * * MON-WED", "с понедельника по среду в 09:00"),
        ("*/15 * * * *", "каждые 15 минут"),
        ("*/2 * * * *", "каждые 2 минуты"),
        ("*/1 * * * *", "каждую минуту"),
        ("*/15 9-18 * * MON-FRI", "по будням каждые 15 минут с 09:00 до 18:59"),
        ("0,30 9-18 * * *", "каждый час в 00 и 30 минут с 09:00 до 18:59"),
        ("*/90 * * * *", "каждый час"),                      # the step starts anew every hour
        ("0 */3 * * *", "каждые 3 ч.: в 00:00, 03:00, 06:00 …"),
        ("0 17 last * *", "в последний день месяца в 17:00"),
        ("0 17 28-31 * *", "каждый день с 28-го по 31-е число месяца в 17:00"),
        ("0 9 1,15 * *", "каждый месяц 1-го и 15-го числа в 09:00"),
        ("0 15 29 10 *", "каждый год 29 октября в 15:00"),
        ("0,30 0-23/3 * * *", "по расписанию «0,30 0-23/3 * * *»"),   # what it cannot say, it shows as it is
    ],
)
def test_a_schedule_reads_as_what_it_does(expr, words):
    assert describe_schedule("cron", expr) == words


@pytest.mark.parametrize("expr", ["0 9 31 4 *", "0 9 30 2 *"])
def test_a_date_that_never_comes_is_refused(expr):
    from common.cron import cron_trigger

    with pytest.raises(ValueError, match="не наступит никогда"):
        cron_trigger(expr)


def test_a_card_says_yes_and_no_not_true_and_false():
    from common.tools import discover
    from compiler.cards import _format_card

    mark = discover()["mail.mark"].manifest()

    assert _format_card(mark["card"], {"account": "Test", "uids": [3], "seen": True, "flagged": False}, fallback="x", schema=mark["input_schema"]) == (
        "Пометить письма 3 в «Test»: прочитано — да, важное — нет"
    )
