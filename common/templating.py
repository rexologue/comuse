"""Templates inside Task specs: `{{ week_start }}`, `{{ files.output.files }}`,
`{{ today | start_of_prev_week }}`. Shared by the compiler (validation) and the
runner (execution) so both read the language the same way.

A string that is exactly one `{{ expr }}` evaluates to the native value (a list
stays a list); anything else renders to a string."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from types import GeneratorType
from typing import Any

from jinja2 import StrictUndefined, TemplateError, meta
from jinja2.sandbox import SandboxedEnvironment


_SINGLE_EXPR = re.compile(r"^\s*\{\{(?P<expr>.*?)\}\}\s*$", re.DOTALL)

BUILTIN_NAMES = {"today", "now", "trigger", "loop", "range", "dict", "lipsum", "cycler", "joiner", "namespace"}


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return date.fromisoformat(str(value)[:10])


def start_of_week(value: Any) -> date:
    d = _as_date(value)
    return d - timedelta(days=d.weekday())


def end_of_week(value: Any) -> date:
    return start_of_week(value) + timedelta(days=6)


def start_of_prev_week(value: Any) -> date:
    return start_of_week(value) - timedelta(days=7)


def end_of_prev_week(value: Any) -> date:
    return start_of_week(value) - timedelta(days=1)


def start_of_month(value: Any) -> date:
    return _as_date(value).replace(day=1)


def end_of_month(value: Any) -> date:
    first_next = (start_of_month(value) + timedelta(days=32)).replace(day=1)
    return first_next - timedelta(days=1)


def start_of_prev_month(value: Any) -> date:
    return start_of_month(start_of_month(value) - timedelta(days=1))


def end_of_prev_month(value: Any) -> date:
    return start_of_month(value) - timedelta(days=1)


def add_days(value: Any, days: int) -> date:
    return _as_date(value) + timedelta(days=int(days))


def format_date(value: Any, fmt: str = "%d.%m.%Y") -> str:
    return _as_date(value).strftime(fmt)


def format_number(value: Any, decimals: int = 0) -> str:
    """1435500.0 → «1 435 500» (Russian grouping with a non-breaking space)."""
    number = float(value)
    text = f"{number:,.{int(decimals)}f}".replace(",", "\u00a0")

    return text.replace(".", ",") if decimals else text


FILTERS = {
    "start_of_week": start_of_week,
    "end_of_week": end_of_week,
    "start_of_prev_week": start_of_prev_week,
    "end_of_prev_week": end_of_prev_week,
    "start_of_month": start_of_month,
    "end_of_month": end_of_month,
    "start_of_prev_month": start_of_prev_month,
    "end_of_prev_month": end_of_prev_month,
    "add_days": add_days,
    "format_date": format_date,
    "format_number": format_number,
}


def make_env() -> SandboxedEnvironment:
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.filters.update(FILTERS)

    return env


_ENV = make_env()


def has_template(value: Any) -> bool:
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def render(value: Any, ctx: dict[str, Any]) -> Any:
    """Render templates anywhere inside a JSON-like value."""
    if isinstance(value, str):
        return render_str(value, ctx)

    if isinstance(value, list):
        return [render(item, ctx) for item in value]

    if isinstance(value, dict):
        return {key: render(item, ctx) for key, item in value.items()}

    return value


def render_str(text: str, ctx: dict[str, Any]) -> Any:
    if not has_template(text):
        return text

    match = _SINGLE_EXPR.match(text)
    if match and "{{" not in match.group("expr"):
        return jsonable_lazy(evaluate(match.group("expr"), ctx))

    return _ENV.from_string(text).render(ctx)


def evaluate(expr: str, ctx: dict[str, Any]) -> Any:
    """Evaluate a bare expression; `{{ … }}` around it is tolerated."""
    match = _SINGLE_EXPR.match(expr)
    if match:
        expr = match.group("expr")

    return _ENV.compile_expression(expr.strip(), undefined_to_none=False)(**ctx)


def referenced_names(value: Any) -> set[str]:
    """Top-level names a template reads (`files` in `{{ files.output }}`); templates
    that do not compile contribute nothing (check_syntax reports them)."""
    names: set[str] = set()

    if isinstance(value, str) and has_template(value):
        try:
            names |= meta.find_undeclared_variables(_ENV.parse(value))

        except TemplateError:
            pass

    elif isinstance(value, list):
        for item in value:
            names |= referenced_names(item)

    elif isinstance(value, dict):
        for item in value.values():
            names |= referenced_names(item)

    return names


def expression_names(expr: str) -> set[str]:
    return referenced_names(_as_template(expr))


def check_syntax(value: Any) -> list[str]:
    """Compile every template: parsing alone misses unknown filters and tests."""
    errors: list[str] = []

    if isinstance(value, str) and has_template(value):
        try:
            _ENV.from_string(value)

        except TemplateError as e:
            hint = f". Доступные фильтры дат: {', '.join(FILTERS)}" if "filter" in str(e).lower() else ""
            errors.append(f"{value!r}: {e}{hint}")

    elif isinstance(value, list):
        for item in value:
            errors += check_syntax(item)

    elif isinstance(value, dict):
        for item in value.values():
            errors += check_syntax(item)

    return errors


def check_expression(expr: str) -> list[str]:
    return check_syntax(_as_template(expr))


def _as_template(expr: str) -> str:
    match = _SINGLE_EXPR.match(expr)
    body = match.group("expr") if match else expr

    return "{{ " + body.strip() + " }}"


def jsonable_lazy(value: Any) -> Any:
    """Materialize a lazy Jinja result so it can be reused and serialized."""
    if isinstance(value, (GeneratorType, map, filter)):
        return list(value)

    return value


def jsonable(value: Any) -> Any:
    """Dates become ISO strings and lazy sequences (`map`, `select` results are
    generators in Jinja) become lists before a value leaves the runner."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, (GeneratorType, map, filter, tuple)):
        return [jsonable(item) for item in value]

    if isinstance(value, list):
        return [jsonable(item) for item in value]

    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}

    return value


__all__ = [
    "BUILTIN_NAMES",
    "check_expression",
    "check_syntax",
    "evaluate",
    "expression_names",
    "has_template",
    "jsonable",
    "referenced_names",
    "render",
    "render_str",
]
