"""ORM models. The comment above each model names the only service allowed to write it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.storage.db import Base
from common.utils import utcnow


# -----------------------------
# MAIN
# -----------------------------

class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # version_approve: the user's approval of a task version, the only one there is
    kind: Mapped[str] = mapped_column(String, index=True)
    # pending | approved | rejected | cancelled (a newer draft replaced the version)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    task_id: Mapped[str | None] = mapped_column(String, index=True)
    task_version_id: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """A value the user set through the API (common/tunables.py); no row = the default."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# -----------------------------
# AGENT LOOP
# -----------------------------

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    # user | assistant | tool | notice (a task run's message)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[Any] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String)
    name: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TraceEvent(Base):
    __tablename__ = "trace_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    message_id: Mapped[int | None] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String)
    args: Mapped[Any] = mapped_column(JSON, default=dict)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    # ok | error | needs_approval (waits for the user) | confirmed (ran after their yes)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# -----------------------------
# COMPILER
# -----------------------------

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default="")
    # draft | active | paused | archived
    status: Mapped[str] = mapped_column(String, default="draft", index=True)
    current_version_id: Mapped[str | None] = mapped_column(String)
    # Domain settings sections ({"mail": {"send_directly": false}}); see compiler/task_settings.py
    settings: Mapped[Any] = mapped_column(JSON, default=dict)
    source_session_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TaskVersion(Base):
    __tablename__ = "task_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    spec: Mapped[Any] = mapped_column(JSON)
    # draft | approved | superseded | rejected
    status: Mapped[str] = mapped_column(String, default="draft")
    parent_version_id: Mapped[str | None] = mapped_column(String)
    patch: Mapped[Any] = mapped_column(JSON, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    cards: Mapped[Any] = mapped_column(JSON, default=list)
    # compile | patch | manual
    created_by: Mapped[str] = mapped_column(String, default="compile")
    # The chat the version came from (compile, a patch asked in chat); None for manual edits.
    session_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------
# RUNNER
# -----------------------------

RUN_FINISHED = ("succeeded", "empty", "failed", "cancelled")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    task_version_id: Mapped[str] = mapped_column(String)
    trigger: Mapped[str] = mapped_column(String, default="manual")
    # queued | running | succeeded | empty | failed | cancelled
    # empty: succeeded, but every step came back empty (a scheduled check found nothing)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    # Last step's output and the files the run made; everything else is in run_log.
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunLog(Base):
    """The one log of a run, append-only: start, every step (arguments, output, error
    with traceback, the tool's own messages), notifications, end."""

    __tablename__ = "run_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # start | step | notify | end
    kind: Mapped[str] = mapped_column(String)
    step_key: Mapped[str | None] = mapped_column(String)
    data: Mapped[Any] = mapped_column(JSON, default=dict)


# -----------------------------
# EXECUTOR
# -----------------------------

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # A kind a tool group registers (common/tools/accounts.py), e.g. mail's imap_smtp.
    kind: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, unique=True)
    # Non-secret connection settings; the secret itself lives in the keyring.
    config: Mapped[Any] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
