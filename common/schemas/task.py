"""Task specification: the frozen scenario the compiler produces and the runner executes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


STEP_ID_PATTERN = r"^[a-z][a-z0-9_]{0,40}$"


class Trigger(BaseModel):
    """Only *when* the task runs; what to look at (mail, folders) is up to its steps."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["manual", "cron"] = "manual"
    # cron: five-field crontab, e.g. "0 10 * * MON".
    expr: str | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> Trigger:
        if self.type == "cron" and not self.expr:
            raise ValueError("cron trigger requires `expr`")

        return self


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=STEP_ID_PATTERN)
    title: str | None = None
    tool: str | None = None
    # External tools (ext.*) only: the approved version the step runs. The compiler sets
    # it, so a new version of the tool changes nothing here until the user moves the
    # task to it (docs/EXTERNAL_TOOLS.md).
    tool_version: int | None = Field(default=None, ge=1)
    args: dict[str, Any] = Field(default_factory=dict)
    when: str | None = None
    foreach: str | None = None
    as_: str = Field(default="item", alias="as", pattern=STEP_ID_PATTERN)
    steps: list[Step] | None = None
    # A run never waits for a person: an error fails, retries or skips; an empty result
    # continues, fails or ends the run quietly.
    on_error: Literal["fail", "retry", "skip"] = "fail"
    on_empty: Literal["continue", "fail", "skip"] = "continue"
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
