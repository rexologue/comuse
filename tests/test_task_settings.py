"""Task settings: a tool declares them with defaults; the user changes them for the whole
task or for one step; each step runs on its own value, else the task's, else the default."""

from __future__ import annotations

import pytest

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.tools import discover
from common.tools.settings import resolve
from compiler import task_settings


CATALOG = {name: spec.manifest() for name, spec in discover().items()}

# Two letters and a file listing: only mail.send has settings.
TASK = TaskSpec.model_validate({
    "title": "t",
    "steps": [
        {"id": "files", "tool": "fs.list", "args": {}},
        {"id": "to_boss", "tool": "mail.send", "args": {}},
        {"id": "to_client", "tool": "mail.send", "args": {}},
    ],
})
FILES_TASK = TaskSpec.model_validate({"title": "t", "steps": [{"id": "files", "tool": "fs.list", "args": {}}]})

SEND = {"tool": "mail.send", "field": "send_directly"}


def test_nothing_changed_is_nothing_stored_and_every_step_runs_on_the_default():
    settings = task_settings.sync({}, TASK, CATALOG)

    assert settings == {}
    assert resolve(settings, "mail.send", "to_boss") == {}

    [tool] = task_settings.view(settings, TASK, CATALOG, [])
    [field] = tool["fields"]
    assert (tool["tool"], field["default"], field["all"]) == ("mail.send", False, None)
    assert [(s["step"], s["value"], s["effective"]) for s in field["steps"]] == [("to_boss", None, False), ("to_client", None, False)]


def test_the_task_value_covers_every_step_and_a_step_can_differ():
    settings = task_settings.apply({}, [{**SEND, "value": True}, {**SEND, "step": "to_client", "value": False}], TASK, CATALOG)

    assert settings == {"mail": {"mail.send": {"send_directly": {"all": True, "steps": {"to_client": False}}}}}
    assert resolve(settings, "mail.send", "to_boss") == {"send_directly": True}
    assert resolve(settings, "mail.send", "to_client") == {"send_directly": False}

    [field] = task_settings.view(settings, TASK, CATALOG, [])[0]["fields"]
    assert [s["effective"] for s in field["steps"]] == [True, False]


def test_null_takes_a_change_back():
    settings = task_settings.apply({}, [{**SEND, "value": True}, {**SEND, "step": "to_client", "value": False}], TASK, CATALOG)

    settings = task_settings.apply(settings, [{**SEND, "step": "to_client", "value": None}], TASK, CATALOG)
    assert resolve(settings, "mail.send", "to_client") == {"send_directly": True}

    assert task_settings.apply(settings, [{**SEND, "value": None}], TASK, CATALOG) == {}


def test_a_new_version_keeps_what_still_fits():
    settings = task_settings.apply({}, [{**SEND, "value": True}, {**SEND, "step": "to_client", "value": False}], TASK, CATALOG)

    # The client letter is gone: its own value goes, the task's stays.
    one_letter = TaskSpec.model_validate({"title": "t", "steps": [{"id": "to_boss", "tool": "mail.send", "args": {}}]})
    assert task_settings.sync(settings, one_letter, CATALOG) == {"mail": {"mail.send": {"send_directly": {"all": True}}}}

    # No letters at all: nothing is left.
    assert task_settings.sync(settings, FILES_TASK, CATALOG) == {}


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "fs.list", "field": "x", "value": 1},                         # a tool without settings
        {**SEND, "field": "send_later", "value": True},                         # no such field
        {**SEND, "step": "files", "value": True},                               # a step of another tool
        {**SEND, "value": "yes"},                                               # not a boolean
    ],
)
def test_changes_are_checked_all_or_nothing(change):
    with pytest.raises(ApiError) as e:
        task_settings.apply({}, [{**SEND, "value": True}, change], TASK, CATALOG)

    assert e.value.status_code == 422 and len(e.value.details["errors"]) == 1


def test_the_agent_tells_the_task_values_in_words():
    settings = task_settings.apply({}, [{**SEND, "value": True}], TASK, CATALOG)

    assert task_settings.lines(task_settings.view({}, TASK, CATALOG, []))[0].startswith("Отправлять письма сразу: выключено (по умолчанию).")
    assert task_settings.lines(task_settings.view(settings, TASK, CATALOG, []))[0].startswith("Отправлять письма сразу: включено.")
