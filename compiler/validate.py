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


_OUTPUT_REF = re.compile(r"\b([a-z][a-z0-9_]*)\.output((?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

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
    _check_steps(spec.steps, scope, {}, catalog, errors, path="steps")

    if errors:
        raise SpecError(errors)

    return spec


def _check_steps(
    steps: list[Step],
    scope: set[str],
    outputs: dict[str, dict[str, Any] | None],
    catalog: dict[str, dict[str, Any]],
    errors: list[str],
    path: str,
) -> None:
    """Each step may reference variables and the steps before it (in its scope).
    `outputs` maps visible step ids to their tools' output schemas."""
    visible = set(scope)
    outputs = dict(outputs)

    for i, step in enumerate(steps):
        where = f"{path}[{i}] ({step.id})"

        if step.when:
            errors += [f"{where}.when: {err}" for err in templating.check_expression(step.when)]
            _check_names(templating.expression_names(step.when), visible, f"{where}.when", errors)
            _check_output_refs(step.when, outputs, catalog, f"{where}.when", errors)

        if step.foreach is not None:
            errors += [f"{where}.foreach: {err}" for err in templating.check_expression(step.foreach)]
            _check_names(templating.expression_names(step.foreach), visible, f"{where}.foreach", errors)
            _check_output_refs(step.foreach, outputs, catalog, f"{where}.foreach", errors)
            _check_steps(step.steps or [], visible | {step.as_}, outputs, catalog, errors, f"{where}.steps")
            # A foreach yields a list of per-item results; its fields are not checked.
            outputs[step.id] = None

        else:
            _check_tool_step(step, visible, catalog, errors, where)
            _check_output_refs(step.args, outputs, catalog, f"{where}.args", errors)
            outputs[step.id] = catalog.get(step.tool or "", {}).get("output_schema")

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


def _check_output_refs(
    value: Any,
    outputs: dict[str, dict[str, Any] | None],
    catalog: dict[str, dict[str, Any]],
    where: str,
    errors: list[str],
) -> None:
    """`files.output.files` must exist in fs.list's output schema, and so on down."""
    for text in _strings(value):
        for step_id, tail in _OUTPUT_REF.findall(text):
            schema = outputs.get(step_id)
            if schema is None:
                continue

            node, defs = schema, schema.get("$defs", {})
            for field in [f for f in tail.split(".") if f]:
                node = _resolve(node, defs)
                properties = node.get("properties")

                # Free-form results (llm.extract data, dicts) cannot be checked further.
                if not properties:
                    break

                if field not in properties:
                    errors.append(
                        f"{where}: ссылка `{step_id}.output{tail}` — в результате нет поля `{field}`; "
                        f"есть: {sorted(properties)}"
                    )
                    break

                node = properties[field]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if templating.has_template(value) or ".output" in value else []

    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]

    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]

    return []


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref", "")
    if ref.startswith("#/$defs/"):
        return defs.get(ref.split("/")[-1], {})

    if "anyOf" in node:
        for option in node["anyOf"]:
            resolved = _resolve(option, defs)
            if resolved.get("properties"):
                return resolved

    return node


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
