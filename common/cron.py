"""Cron expressions of task triggers: five fields, days of week by name.

APScheduler numbers days of week from Monday (0 = mon, 1 = tue), standard cron from
Sunday (0 = sun, 1 = mon): `0 9 * * 1` would run on Tuesdays while every reader takes it
for Mondays. Numbers in that field are refused; names (MON..SUN) mean the same to all.
Shared by the compiler (validation, cards), the scheduler (jobs) and main (a task's
schedule in words)."""

from __future__ import annotations

import re

from apscheduler.triggers.cron import CronTrigger


def cron_trigger(expr: str) -> CronTrigger:
    """Raises ValueError with a message the user (or the model) can act on."""
    fields = expr.split()

    if len(fields) == 5 and re.search(r"\d", fields[4]):
        raise ValueError(
            f"дни недели — только именами MON, TUE, WED, THU, FRI, SAT, SUN (например MON-FRI), "
            f"не числами: «{fields[4]}» планировщик понял бы не так, как обычный cron"
        )

    return CronTrigger.from_crontab(expr)


def describe_schedule(trigger_type: str | None, expr: str | None) -> str:
    """A trigger in words: «по будням в 09:00», «только вручную»."""
    if trigger_type == "cron":
        return _describe_cron(expr or "")

    return "только вручную"


# Days of week are names only (cron_trigger refuses numbers).
_DAYS = {
    "MON": "понедельникам", "TUE": "вторникам", "WED": "средам", "THU": "четвергам",
    "FRI": "пятницам", "SAT": "субботам", "SUN": "воскресеньям",
}


def _describe_cron(expr: str) -> str:
    parts = expr.split()
    if len(parts) != 5:
        return f"по расписанию «{expr}»"

    minute, hour, dom, month, dow = parts

    if not (minute.isdigit() and hour.isdigit()):
        return f"по расписанию «{expr}»"

    at = f"в {int(hour):02d}:{int(minute):02d}"

    if dom == "*" and month == "*" and dow == "*":
        return f"каждый день {at}"

    if dom == "*" and month == "*":
        days = [_DAYS.get(d.upper(), d) for d in dow.split(",")]
        if dow.upper() == "MON-FRI":
            return f"по будням {at}"

        return f"по {', '.join(days)} {at}"

    if dom.isdigit() and month == "*" and dow == "*":
        return f"каждый месяц {int(dom)}-го числа {at}"

    return f"по расписанию «{expr}»"
