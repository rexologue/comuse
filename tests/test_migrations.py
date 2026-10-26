"""The schema comes from the migrations; they must build exactly what the models describe."""

from __future__ import annotations

import json

import pytest
from alembic import command
from sqlalchemy import text

from common.settings import Settings
from common.storage import migrate
from common.storage.db import init_engine, session
from common.storage.models import Event


def test_the_migrations_build_what_the_models_describe(settings):
    """Fails when a model changed without a migration: `python -m common.storage.migrate new "…"`."""
    engine = init_engine(settings.storage)

    assert migrate.current_revision(engine) == migrate.head()
    assert migrate.differences(engine) == []


def test_a_database_from_before_migrations_is_taken_as_the_baseline(env):
    storage = Settings.from_env("main").storage
    engine = init_engine(storage)

    # What create_all made then: the baseline schema with no version recorded.
    migrate._alembic(engine, command.upgrade, migrate.BASELINE)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))

    with session() as db:
        db.add(Event(type="run.finished", payload={}))
        db.commit()

    migrate.upgrade(engine, storage)

    assert migrate.current_revision(engine) == migrate.head()
    assert list((storage.data_dir / "backups").glob(f"app-{migrate.BASELINE}-*.db"))

    with session() as db:
        assert db.query(Event).count() == 1


def test_a_service_does_not_start_on_an_older_schema(env):
    engine = init_engine(Settings.from_env("runner").storage)

    with pytest.raises(RuntimeError, match="start main first"):
        migrate.wait_current(engine, timeout=0)


def test_backups_keep_only_the_latest_copies(settings):
    backups = settings.storage.data_dir / "backups"
    backups.mkdir()

    for day in range(1, 8):
        (backups / f"app-0001-2026010{day}-090000.db").touch()

    migrate._backup(settings.storage, "0001")

    copies = sorted(p.name for p in backups.glob("app-*.db"))

    assert len(copies) == 5
    assert "app-0001-20260101-090000.db" not in copies
    assert (backups / copies[-1]).stat().st_size > 0


def test_old_own_commands_get_a_group_and_their_folders_move(env):
    """Commands from before group.action names become ext.<name>, files and all."""
    storage = Settings.from_env("main").storage
    engine = init_engine(storage)
    migrate._alembic(engine, command.upgrade, "0005")

    with engine.begin() as connection:
        connection.execute(text("INSERT INTO external_tools (name, current_version, created_at, updated_at) VALUES ('parse_date', 1, '2026-01-01', '2026-01-01')"))
        connection.execute(text(
            "INSERT INTO external_tool_versions (id, name, version, status, hash, manifest, packages, created_by, created_at, updated_at) "
            "VALUES ('etv_1', 'parse_date', 1, 'approved', 'h', '{}', '[]', 'user', '2026-01-01', '2026-01-01')"
        ))

    (storage.external_tools_dir / "parse_date" / "1").mkdir(parents=True)
    (storage.external_tools_dir / "parse_date" / "1" / "main.py").write_text("")

    migrate.upgrade(engine, storage)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM external_tools")).scalar() == "ext.parse_date"
        assert connection.execute(text("SELECT name FROM external_tool_versions")).scalar() == "ext.parse_date"

    assert (storage.external_tools_dir / "ext" / "parse_date" / "1" / "main.py").is_file()
    assert not (storage.external_tools_dir / "parse_date").exists()


def test_task_settings_move_to_changes_per_tool(env):
    storage = Settings.from_env("main").storage
    engine = init_engine(storage)
    migrate._alembic(engine, command.upgrade, "0006")

    with engine.begin() as connection:
        for task_id, settings in (("task_on", '{"mail": {"send_directly": true}}'), ("task_default", '{"mail": {"send_directly": false}}')):
            connection.execute(text(
                "INSERT INTO tasks (id, title, description, status, settings, created_at, updated_at) "
                "VALUES (:id, 't', '', 'active', :settings, '2026-01-01', '2026-01-01')"
            ), {"id": task_id, "settings": settings})

    migrate.upgrade(engine, storage)

    with engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT id, settings FROM tasks")).all())

    assert json.loads(rows["task_on"]) == {"mail": {"mail.send": {"send_directly": {"all": True}}}}
    assert json.loads(rows["task_default"]) == {}
