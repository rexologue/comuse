"""Static checks of a Task spec against the tool catalog. Every error message is
written so the LLM can fix the spec from it."""

from __future__ import annotations

import re
from typing import Any

import jsonschema
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError

from common import templating
from common.schemas.task import Step, TaskSpec


_ABSOLUTE_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|/(?:home|Users|mnt|tmp|var|etc)/|~[\\/])")


class SpecError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(f"- {e}" for e in errors))

        self.errors = errors


def validate_spec(data: Any, catalog: dict[str, dict[str, Any]]) -> TaskSpec:
    """Parse and check a spec; raises SpecError listing every problem found."""
    try:
        spec = TaskSpec.model_validate(data)

    except ValidationError as e:
        raise SpecError([_pydantic_error(err) for err in e.errors()]) from e

    errors: list[str] = []

    if spec.trigger.type == "cron":
        try:
            CronTrigger.from_crontab(spec.trigger.expr)

        except ValueError as e:
            errors.append(f"trigger.expr {spec.trigger.expr!r} — неверный cron: {e}")

    variable_names = set(spec.variables)
    for name, expr in spec.variables.items():
        errors += [f"variables.{name}: {err}" for err in templating.check_syntax(expr)]

        unknown = templating.referenced_names(expr) - templating.BUILTIN_NAMES - variable_names
        if unknown:
            errors.append(f"variables.{name}: неизвестные имена {sorted(unknown)}")

    scope = templating.BUILTIN_NAMES | variable_names
    _check_steps(spec.steps, scope, catalog, errors, path="steps")

    if errors:
        raise SpecError(errors)

    return spec


def _check_steps(
    steps: list[Step],
    scope: set[str],
    catalog: dict[str, dict[str, Any]],
    errors: list[str],
    path: str,
) -> None:
    """Each step may reference variables and the steps before it (in its scope)."""
    visible = set(scope)

    for i, step in enumerate(steps):
        where = f"{path}[{i}] ({step.id})"

        if step.when:
            _check_names(templating.expression_names(step.when), visible, f"{where}.when", errors)

        if step.foreach is not None:
            _check_names(templating.expression_names(step.foreach), visible, f"{where}.foreach", errors)
            _check_steps(step.steps or [], visible | {step.as_}, catalog, errors, f"{where}.steps")

        else:
            _check_tool_step(step, visible, catalog, errors, where)

        visible.add(step.id)


def _check_tool_step(
    step: Step,
    visible: set[str],
    catalog: dict[str, dict[str, Any]],
    errors: list[str],
    where: str,
) -> None:
    manifest = catalog.get(step.tool)
    if manifest is None:
        errors.append(f"{where}: инструмента `{step.tool}` нет; доступны: {sorted(catalog)}")
        return

    errors += [f"{where}.args: {err}" for err in templating.check_syntax(step.args)]
    _check_names(templating.referenced_names(step.args), visible, f"{where}.args", errors)

    schema = manifest["input_schema"]
    properties = schema.get("properties", {})

    unknown = set(step.args) - set(properties)
    if unknown:
        errors.append(f"{where}: у `{step.tool}` нет аргументов {sorted(unknown)}; есть {sorted(properties)}")

    missing = set(schema.get("required", [])) - set(step.args)
    if missing:
        errors.append(f"{where}: для `{step.tool}` обязательны аргументы {sorted(missing)}")

    for key, value in step.args.items():
        if key not in properties:
            continue

        if _contains_template(value):
            continue

        if isinstance(value, str) and _ABSOLUTE_PATH.match(value):
            errors.append(f"{where}.args.{key}: абсолютный путь {value!r}; используй путь относительно рабочей папки")

        try:
            jsonschema.validate(value, {**properties[key], "$defs": schema.get("$defs", {})})

        except jsonschema.ValidationError as e:
            errors.append(f"{where}.args.{key}: {e.message}")


def _check_names(names: set[str], visible: set[str], where: str, errors: list[str]) -> None:
    unknown = names - visible
    if unknown:
        errors.append(
            f"{where}: ссылки на {sorted(unknown)} недоступны здесь; можно ссылаться на переменные "
            f"и на шаги выше: {sorted(visible - templating.BUILTIN_NAMES)}"
        )


def _contains_template(value: Any) -> bool:
    if isinstance(value, str):
        return templating.has_template(value)

    if isinstance(value, list):
        return any(_contains_template(v) for v in value)

    if isinstance(value, dict):
        return any(_contains_template(v) for v in value.values())

    return False


def _pydantic_error(err: dict[str, Any]) -> str:
    loc = ".".join(str(p) for p in err.get("loc", ()))
    return f"{loc or 'spec'}: {err.get('msg')}"
