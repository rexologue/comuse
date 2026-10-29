"""Cron expressions of task triggers: five fields, days of week by name.

APScheduler numbers days of week from Monday (0 = mon, 1 = tue), standard cron from
Sunday (0 = sun, 1 = mon): `0 9 * * 1` would run on Tuesdays while every reader takes it
for Mondays. Numbers in that field are refused; names (MON..SUN) mean the same to all.
Shared by the compiler (validation, cards), the scheduler (jobs) and main (a task's
schedule in words)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger


def cron_trigger(expr: str) -> CronTrigger:
    """Raises ValueError with a message the user (or the model) can act on."""
    fields = expr.split()

    if len(fields) == 5 and re.search(r"\d", fields[4]):
        raise ValueError(
            f"дни недели — только именами MON, TUE, WED, THU, FRI, SAT, SUN (например MON-FRI), "
            f"не числами: «{fields[4]}» планировщик понял бы не так, как обычный cron"
        )

    trigger = CronTrigger.from_crontab(expr)

    # 31 April, 30 February: a schedule that is accepted and never comes.
    if trigger.get_next_fire_time(None, datetime.now(timezone.utc)) is None:
        raise ValueError(f"«{expr}» не наступит никогда: такой даты нет (например, 31 апреля или 30 февраля)")

    return trigger


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
# «с понедельника по среду».
_DAY_FROM = {
    "MON": "понедельника", "TUE": "вторника", "WED": "среды", "THU": "четверга",
    "FRI": "пятницы", "SAT": "субботы", "SUN": "воскресенья",
}
_DAY_TO = {
    "MON": "понедельник", "TUE": "вторник", "WED": "среду", "THU": "четверг",
    "FRI": "пятницу", "SAT": "субботу", "SUN": "воскресенье",
}
_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")

_NUM = re.compile(r"^\d+$")
_RANGE = re.compile(r"^(\d+)-(\d+)$")
_STEP = re.compile(r"^(\*|\d+-\d+)/(\d+)$")


def _describe_cron(expr: str) -> str:
    """Every schedule in words that say what it does — «каждые 15 минут с 09:00 до 18:59 по
    будням», «в последний день месяца в 17:00» — or, for one it cannot put so, the
    expression itself: never words that mean something else."""
    parts = expr.split()
    if len(parts) != 5:
        return f"по расписанию «{expr}»"

    minute, hour, dom, month, dow = parts
    when, days = _times(minute, hour), _days(dom, month, dow.upper())

    if when is None or days is None:
        return f"по расписанию «{expr}»"

    # «каждые 15 минут», not «каждый день каждые 15 минут».
    if days == "каждый день" and when.startswith("кажд"):
        return when

    return f"{days} {when}"


def _times(minute: str, hour: str) -> str | None:
    hours = _hours(hour)

    if _NUM.match(minute) and _NUM.match(hour):
        return f"в {int(hour):02d}:{int(minute):02d}"

    if _NUM.match(minute) and all(_NUM.match(h) for h in hour.split(",")):
        times = [f"{int(h):02d}:{int(minute):02d}" for h in hour.split(",")]
        return "в " + ", ".join(times[:-1]) + f" и {times[-1]}"

    # Every N hours: from midnight, the step starting anew each day.
    if _NUM.match(minute) and (step := _STEP.match(hour)) and step.group(1) == "*":
        n = int(step.group(2))
        times = [f"{h:02d}:{int(minute):02d}" for h in range(0, 24, n)]
        return f"каждые {n} ч.: в {', '.join(times[:3])}{' …' if len(times) > 3 else ''}"

    if hours is None:
        return None

    if _NUM.match(minute):
        at = "каждый час" if int(minute) == 0 else f"каждый час в {int(minute):02d} минут"
    elif (step := _STEP.match(minute)) and step.group(1) == "*" and 60 % int(step.group(2)) == 0:
        n = int(step.group(2))
        at = "каждую минуту" if n == 1 else f"каждые {n} минуты" if n in (2, 3, 4) else f"каждые {n} минут"
    elif step:
        # */45 is 00 and 45 of every hour, */90 only 00: the step starts anew each hour.
        at = _minutes([str(m) for m in range(0, 60, int(step.group(2)))]) if step.group(1) == "*" else None
    elif minute == "*":
        at = "каждую минуту"
    elif all(_NUM.match(m) for m in minute.split(",")):
        at = _minutes(minute.split(","))
    else:
        return None

    if at is None:
        return None

    return f"{at}{hours}"


def _minutes(minutes: list[str]) -> str:
    if len(minutes) == 1:
        return "каждый час" if int(minutes[0]) == 0 else f"каждый час в {int(minutes[0]):02d} минут"

    shown = [f"{int(m):02d}" for m in minutes]
    return "каждый час в " + ", ".join(shown[:-1]) + f" и {shown[-1]} минут"


def _hours(hour: str) -> str | None:
    """The hours a sub-hourly schedule runs in: «» for all day, « с 09:00 до 18:59»."""
    if hour == "*":
        return ""

    if match := _RANGE.match(hour):
        return f" с {int(match.group(1)):02d}:00 до {int(match.group(2)):02d}:59"

    return None


def _days(dom: str, month: str, dow: str) -> str | None:
    if month != "*":
        if _NUM.match(month) and _NUM.match(dom) and dow == "*" and 1 <= int(month) <= 12:
            return f"каждый год {int(dom)} {_MONTHS[int(month) - 1]}"
        return None

    if dom == "*":
        if dow == "*":
            return "каждый день"
        if dow == "MON-FRI":
            return "по будням"
        if dow in ("SAT,SUN", "SAT-SUN"):
            return "по выходным"
        if match := re.match(r"^([A-Z]{3})-([A-Z]{3})$", dow):
            if match.group(1) in _DAY_FROM and match.group(2) in _DAY_TO:
                return f"с {_DAY_FROM[match.group(1)]} по {_DAY_TO[match.group(2)]}"
            return None
        names = dow.split(",")
        if all(name in _DAYS for name in names):
            return "по " + ", ".join(_DAYS[name] for name in names)
        return None

    if dow != "*":
        return None

    if dom.lower() == "last":
        return "в последний день месяца"

    if _NUM.match(dom):
        return f"каждый месяц {int(dom)}-го числа"

    if all(_NUM.match(d) for d in dom.split(",")):
        days = [f"{int(d)}-го" for d in dom.split(",")]
        return "каждый месяц " + ", ".join(days[:-1]) + f" и {days[-1]} числа"

    if match := _RANGE.match(dom):
        return f"каждый день с {int(match.group(1))}-го по {int(match.group(2))}-е число месяца"

    return None
