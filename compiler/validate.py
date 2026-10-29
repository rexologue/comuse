"""Static checks of a Task spec against the tool catalog. Every error message is
written so the LLM can fix the spec from it."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import jsonschema
from jinja2 import nodes
from pydantic import ValidationError

from common import templating
from common.cron import cron_trigger
from common.schemas.task import Step, TaskSpec
from common.utils import strings_in


_OUTPUT_REF = re.compile(r"\b([a-z][a-z0-9_]*)\.output((?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

# C:\…, ~/…, and /a/b or /a.ext: what looks like a path from the root, not «/start».
_ABSOLUTE_PATH = re.compile(r"^(?:[a-zA-Z]:[\\/]|~[\\/]|/[^/\s]+(?:/|\.\w+$))")


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
            cron_trigger(spec.trigger.expr)

        except ValueError as e:
            errors.append(f"trigger.expr {spec.trigger.expr!r} — неверный cron: {e}")

    variable_names = set(spec.variables)
    for name, expr in spec.variables.items():
        errors += [f"variables.{name}: {err}" for err in templating.check_syntax(expr)]

        # Nobody fills it in: a task asks no one anything when it runs.
        if not str(expr).strip():
            errors.append(
                f"variables.{name}: пустое значение — у задачи нет ввода при запуске; значение, которое задал "
                "пользователь, впиши в аргументы шага, а вычисляемое (дата) — выражением"
            )

        unknown = templating.referenced_names(expr) - templating.BUILTIN_NAMES - variable_names
        if unknown:
            errors.append(f"variables.{name}: неизвестные имена {sorted(unknown)}")

    scope = templating.BUILTIN_NAMES | variable_names
    _check_steps(spec.steps, scope, {}, catalog, errors, path="steps", known=_known_before_run(spec))

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
    known: dict[str, Any],
) -> None:
    """Each step may reference variables and the steps before it (in its scope).
    `outputs` maps visible step ids to their tools' output schemas; `known`, the values
    there are before the run (today, now, the variables)."""
    visible = set(scope)
    outputs = dict(outputs)
    # The foreach steps above, by id: the ids of their own steps.
    foreachs: dict[str, list[str]] = {}

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
            _check_steps(step.steps or [], visible | {step.as_}, outputs, catalog, errors, f"{where}.steps", known)
            # A foreach yields a list of per-item results: what an item has (its `as`, its
            # steps, error) and the ways to get at it wrong are checked by _check_foreach_refs.
            outputs[step.id] = None
            foreachs[step.id] = [step.as_, *(child.id for child in step.steps or [])]

        else:
            _check_tool_step(step, visible, catalog, errors, where, known)
            _check_output_refs(step.args, outputs, catalog, f"{where}.args", errors)
            outputs[step.id] = _output_schema(step, catalog)

        _check_foreach_refs(step, foreachs, f"{where}", errors)
        visible.add(step.id)


def _output_schema(step: Step, catalog: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The tool's output schema; a field shaped by an argument (llm.extract's data by its
    output_schema, marked x-from-arg) is that argument as the step gives it."""
    schema = catalog.get(step.tool or "", {}).get("output_schema")
    properties = (schema or {}).get("properties") or {}

    shaped = {}
    for name, node in properties.items():
        given = step.args.get(node.get("x-from-arg")) if isinstance(node, dict) and node.get("x-from-arg") else None
        if isinstance(given, dict) and not _contains_template(given):
            shaped[name] = given

    return {**schema, "properties": {**properties, **shaped}} if shaped else schema


def _known_before_run(spec: TaskSpec) -> dict[str, Any]:
    """today, now and the variables, as a run would have them today."""
    known: dict[str, Any] = {"today": date.today(), "now": datetime.now()}

    for name, expr in spec.variables.items():
        try:
            known[name] = templating.render_str(expr, known)
        except Exception:
            pass  # reported by the variable checks

    return known


def _check_tool_step(
    step: Step,
    visible: set[str],
    catalog: dict[str, dict[str, Any]],
    errors: list[str],
    where: str,
    known: dict[str, Any],
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

        # Anywhere in the value: an attachment in a list, a path in a mapping.
        for text in strings_in(value):
            if not templating.has_template(text) and _ABSOLUTE_PATH.match(text):
                errors.append(f"{where}.args.{key}: абсолютный путь {text!r}; используй путь относительно рабочей папки")

        if _contains_template(value):
            # Made of what there is before the run (a variable): checked as it will be.
            if not templating.referenced_names(value) <= known.keys():
                continue
            try:
                value = templating.jsonable(templating.render(value, known))
            except Exception:
                continue

        if not isinstance(properties[key], dict):
            if properties[key] is False:
                errors.append(f"{where}.args.{key}: аргумент не принимает никаких значений")
            continue

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

                # Free-form results (dicts with no schema) cannot be checked further.
                if not properties:
                    node = {}
                    break

                if field not in properties:
                    errors.append(
                        f"{where}: ссылка `{step_id}.output{tail}` — в результате нет поля `{field}`; "
                        f"есть: {sorted(properties)}"
                    )
                    node = {}
                    break

                node = properties[field]

            # A sum described as a string comes back «$40000»: no number to print or add.
            if _resolve(node, defs).get("type") == "string" and re.search(rf"{step_id}\.output{re.escape(tail)}\s*\|\s*format_number", text):
                errors.append(
                    f"{where}: `{step_id}.output{tail}` выводится как число (format_number), а в схеме это string: "
                    "сделай поле number"
                )


_TEMPLATE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def _check_foreach_refs(step: Step, foreachs: dict[str, list[str]], where: str, errors: list[str]) -> None:
    """A foreach's result is `<id>.output`, a list of {<as>: item, <step id>: its result}:
    `{% for r in sort %}` (no .output) and `r.move.output.path` (a step's result there is
    under its id, not under .output) pass every other check and fail at the run."""
    texts = [step.when or "", step.foreach or "", *_strings(step.args)]

    for text in texts:
        for template in _TEMPLATE.findall(text):
            for foreach_id, children in foreachs.items():
                if re.search(rf"(?<![\w.]){foreach_id}\b(?!\.output)", template):
                    errors.append(
                        f"{where}: `{foreach_id}` — шаг foreach, его результат — `{foreach_id}.output` (список); "
                        f"например {{% for r in {foreach_id}.output %}}"
                    )

                for child in children[1:]:
                    if re.search(rf"[.'\"]{child}\.output\b", template):
                        errors.append(
                            f"{where}: в результате foreach `{foreach_id}` результат шага `{child}` лежит прямо под его id: "
                            f"`r.{child}.<поле>`, без `.output`"
                        )

        for foreach_id, field in _item_fields(text, foreachs):
            known = [*foreachs[foreach_id], "error"]
            if field not in known:
                errors.append(f"{where}: у элемента результата foreach `{foreach_id}` нет поля `{field}`: есть {known}")


def _item_fields(text: str, foreachs: dict[str, list[str]]) -> list[tuple[str, str]]:
    """`r.move` in `{% for r in sort.output %}`: which foreach, which field of its item."""
    if not templating.has_template(text):
        return []

    try:
        tree = templating.make_env().parse(text)

    except Exception:
        return []  # check_syntax reports it

    found = []
    for loop in tree.find_all(nodes.For):
        source = loop.iter
        if not (isinstance(source, nodes.Getattr) and source.attr == "output" and isinstance(source.node, nodes.Name) and source.node.name in foreachs):
            continue
        if not isinstance(loop.target, nodes.Name):
            continue

        for node in loop.find_all(nodes.Getattr):
            if isinstance(node.node, nodes.Name) and node.node.name == loop.target.name:
                found.append((source.node.name, node.attr))

    return found


def _strings(value: Any) -> list[str]:
    """The strings that may refer to other steps."""
    return [s for s in strings_in(value) if templating.has_template(s) or ".output" in s]


def _resolve(node: dict[str, Any] | bool, defs: dict[str, Any]) -> dict[str, Any]:
    # `true` (anything) in a schema a person or the chat wrote: nothing more to check.
    if not isinstance(node, dict):
        return {}

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
