"""Task settings: sections follow the tools a task uses; the user changes values only."""

from __future__ import annotations

import pytest

from common.errors import ApiError
from common.schemas.task import TaskSpec
from common.tools import discover
from compiler import task_settings


CATALOG = {name: spec.manifest() for name, spec in discover().items()}

MAIL_TASK = TaskSpec.model_validate({"title": "t", "steps": [{"id": "send", "tool": "mail.send", "args": {}}]})
FILES_TASK = TaskSpec.model_validate({"title": "t", "steps": [{"id": "files", "tool": "fs.list", "args": {}}]})


def test_a_task_gets_the_defaults_of_the_domains_it_uses():
    assert task_settings.sync({}, MAIL_TASK, CATALOG) == {"mail": {"send_directly": False}}
    assert task_settings.sync({}, FILES_TASK, CATALOG) == {}


def test_a_new_version_keeps_the_users_values_and_drops_unused_domains():
    current = {"mail": {"send_directly": True, "gone": 1}}

    assert task_settings.sync(current, MAIL_TASK, CATALOG) == {"mail": {"send_directly": True}}
    assert task_settings.sync(current, FILES_TASK, CATALOG) == {}


def test_the_user_changes_values():
    current = {"mail": {"send_directly": False}}

    assert task_settings.update(current, {"mail": {"send_directly": True}}, CATALOG) == {"mail": {"send_directly": True}}
    assert current == {"mail": {"send_directly": False}}


@pytest.mark.parametrize(
    "changes",
    [
        {"fs": {"x": 1}},                     # no such section in this task
        {"mail": {"cc_boss": True}},          # no such setting
        {"mail": {"send_directly": "да"}},    # wrong type
    ],
)
def test_keys_cannot_be_added_and_types_are_checked(changes):
    with pytest.raises(ApiError):
        task_settings.update({"mail": {"send_directly": False}}, changes, CATALOG)


def test_settings_are_described_for_the_user():
    described = task_settings.describe({"mail": {"send_directly": False}}, CATALOG)

    assert described["mail"]["send_directly"]["title"] == "Отправлять письма сразу"
    assert described["mail"]["send_directly"]["value"] is False
