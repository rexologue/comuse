"""Task specification: the frozen scenario the compiler produces and the runner executes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"


class Trigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["manual", "cron", "file_appeared", "new_email"] = "manual"
    # cron: five-field crontab, e.g. "0 10 * * MON".
    expr: str | None = None
    # file_appeared: folder relative to the workspace + glob pattern.
    folder: str | None = None
    pattern: str = "*"
    # new_email: account name + IMAP filter (from, subject).
    account: str | None = None
    mail_filter: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_fields(self) -> Trigger:
        if self.type == "cron" and not self.expr:
            raise ValueError("cron trigger requires `expr`")

        if self.type == "file_appeared" and self.folder is None:
            raise ValueError("file_appeared trigger requires `folder`")

        if self.type == "new_email" and not self.account:
            raise ValueError("new_email trigger requires `account`")

        return self


class Permissions(BaseModel):
    """Everything a task may touch. The executor enforces it on every call."""

    model_config = ConfigDict(extra="forbid")

    read_folders: list[str] = Field(default_factory=list)
    write_folders: list[str] = Field(default_factory=list)
    mail_accounts: list[str] = Field(default_factory=list)
    mail_send_to: list[str] = Field(default_factory=list)
    net_domains: list[str] = Field(default_factory=list)


class ApprovalPolicy(BaseModel):
    """When to ask before external side effects (sending mail and alike)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["always", "draft_first_n", "never"] = "draft_first_n"
    n: int = Field(default=3, ge=0)


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=STEP_ID_PATTERN)
    title: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    when: str | None = None
    foreach: str | None = None
    as_: str = Field(default="item", alias="as", pattern=STEP_ID_PATTERN)
    steps: list[Step] | None = None
    on_error: Literal["fail", "retry", "skip", "ask_user"] = "fail"
    on_empty: Literal["continue", "fail", "skip", "ask_user"] = "continue"
    retries: int = Field(default=0, ge=0, le=5)

    @model_validator(mode="after")
    def _check_shape(self) -> Step:
        if self.foreach is not None:
            if not self.steps:
                raise ValueError(f"step `{self.id}`: foreach requires nested `steps`")

            if self.tool is not None:
                raise ValueError(f"step `{self.id}`: foreach step cannot have its own `tool`")

        elif self.tool is None:
            raise ValueError(f"step `{self.id}`: either `tool` or `foreach` + `steps` is required")

        elif self.steps:
            raise ValueError(f"step `{self.id}`: nested `steps` are only allowed with `foreach`")

        return self


    def walk(self):
        """Yield this step and every nested step, depth first."""
        yield self

        for child in self.steps or []:
            yield from child.walk()


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    title: str = Field(min_length=1)
    description: str = ""
    trigger: Trigger = Field(default_factory=Trigger)
    permissions: Permissions = Field(default_factory=Permissions)
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    variables: dict[str, str] = Field(default_factory=dict)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_step_ids(self) -> TaskSpec:
        seen: set[str] = set()

        for step in self.all_steps():
            if step.id in seen:
                raise ValueError(f"duplicate step id `{step.id}`")

            seen.add(step.id)

        return self


    def all_steps(self) -> list[Step]:
        return [s for top in self.steps for s in top.walk()]


    def dump(self) -> dict[str, Any]:
        """JSON form used in storage and API responses (with the `as` alias)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)
