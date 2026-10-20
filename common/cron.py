"""Cron expressions of task triggers: five fields, days of week by name.

APScheduler numbers days of week from Monday (0 = mon, 1 = tue), standard cron from
Sunday (0 = sun, 1 = mon): `0 9 * * 1` would run on Tuesdays while every reader takes it
for Mondays. Numbers in that field are refused; names (MON..SUN) mean the same to all.
Shared by the compiler (validation) and the scheduler (jobs)."""

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
