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

class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # ask_user | confirm_action | version_approve | skill_permissions
    kind: Mapped[str] = mapped_column(String, index=True)
    # pending | approved | rejected | answered | cancelled
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    run_id: Mapped[str | None] = mapped_column(String, index=True)
    task_id: Mapped[str | None] = mapped_column(String, index=True)
    task_version_id: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------
# AGENT LOOP
# -----------------------------

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    # user | assistant | tool
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
    # ok | error | needs_approval | confirmed | rejected
    status: Mapped[str] = mapped_column(String)
    user_feedback: Mapped[str | None] = mapped_column(Text)
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
    dry_run: Mapped[Any] = mapped_column(JSON, nullable=True)
    # compile | patch | manual
    created_by: Mapped[str] = mapped_column(String, default="compile")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------
# SCHEDULER
# -----------------------------

class Trigger(Base):
    __tablename__ = "triggers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)
    config: Mapped[Any] = mapped_column(JSON, default=dict)
    state: Mapped[Any] = mapped_column(JSON, default=dict)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


# -----------------------------
# RUNNER
# -----------------------------

class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, index=True)
    task_version_id: Mapped[str] = mapped_column(String)
    trigger: Mapped[str] = mapped_column(String, default="manual")
    # queued | running | waiting | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    # Resumable engine state: completed step outputs, answers, the pending approval.
    state: Mapped[Any] = mapped_column(JSON, default=dict)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    step_key: Mapped[str] = mapped_column(String)
    step_id: Mapped[str] = mapped_column(String)
    tool: Mapped[str | None] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # running | ok | error | skipped | waiting
    status: Mapped[str] = mapped_column(String)
    input: Mapped[Any] = mapped_column(JSON, nullable=True)
    output: Mapped[Any] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# -----------------------------
# EXECUTOR
# -----------------------------

class Tool(Base):
    __tablename__ = "tools"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    # internal | skill | mcp
    source: Mapped[str] = mapped_column(String, default="internal")
    version: Mapped[str] = mapped_column(String, default="1")
    manifest: Mapped[Any] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    # draft | approved | disabled
    status: Mapped[str] = mapped_column(String, default="draft")
    manifest: Mapped[Any] = mapped_column(JSON)
    path: Mapped[str] = mapped_column(String)
    source_session_id: Mapped[str | None] = mapped_column(String)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    # imap_smtp | llm
    kind: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, unique=True)
    # Non-secret connection settings; the secret itself lives in the keyring.
    config: Mapped[Any] = mapped_column(JSON, default=dict)
    secret_ref: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tool: Mapped[str] = mapped_column(String, index=True)
    mode: Mapped[str] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String)
    run_id: Mapped[str | None] = mapped_column(String, index=True)
    step_key: Mapped[str | None] = mapped_column(String)
    args: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String, index=True)
    session_id: Mapped[str | None] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    mime: Mapped[str] = mapped_column(String, default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
