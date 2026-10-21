"""Settings the user changes while the agent runs (GET/PATCH /settings in main).

A value comes from, in this order: the environment variable of the same name in upper
case (it locks the setting: the API cannot change it), the `settings` table, the
default here. Values are read on every use, so a change applies without a restart.
Everything the services need before the database opens (ports, tokens, DATA_DIR,
LLM) stays in common/settings.py and the environment only."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.settings import SettingsError, path_coercion
from common.storage.db import session
from common.storage.models import Setting


log = logging.getLogger("tunables")


@dataclass(slots=True, frozen=True)
class Tunable:
    key: str
    kind: str          # "int" | "bool" | "choice" | "dir"
    default: Any
    description: str
    low: int | None = None
    high: int | None = None
    choices: tuple[str, ...] | None = None

    @property
    def env(self) -> str:
        return self.key.upper()


    def parse(self, value: Any) -> Any:
        """The value as code uses it; ValueError says what is wrong in words."""
        if self.kind == "dir":
            return _parse_dir(value)

        if self.kind == "bool":
            return _parse_bool(value)

        if self.kind == "choice":
            return _parse_choice(value, self.choices)

        return _parse_int(value, self.low, self.high)


    def to_json(self, value: Any) -> Any:
        return str(value) if self.kind == "dir" else value


TUNABLES = {
    t.key: t
    for t in (
        Tunable("workspace_dir", "dir", "workspace",
                "Рабочая папка: документы пользователя. Команды не выходят за её пределы"),
        Tunable("retention_days", "int", 15,
                "Сколько дней хранятся запуски с журналами, файлы результатов и события", low=1),
        Tunable("agent_max_iterations", "int", 16,
                "Сколько раз за один ход чата модель может вызвать команды", low=1),
        Tunable("agent_tools_top_k", "int", 24,
                "Сколько команд, ближайших к запросу, видит модель (при модели эмбеддингов)", low=1),
        Tunable("agent_history_messages", "int", 40,
                "Сколько последних сообщений чата видит модель", low=2),
        Tunable("compiler_max_attempts", "int", 3,
                "Сколько попыток у компилятора собрать задачу, проходящую проверки", low=1),
        Tunable("tool_timeout_sec", "int", 300,
                "Сколько секунд может выполняться одна команда", low=1),
        Tunable("llm_timeout_sec", "int", 300,
                "Сколько секунд ждать ответа LLM", low=1),
        Tunable("llm_max_retries", "int", 2,
                "Сколько раз повторить запрос к LLM при сетевом сбое", low=0),
        Tunable("llm_no_proxy", "bool", True,
                "Ходить к LLM напрямую, не через прокси из HTTP(S)_PROXY"),
        Tunable("llm_chat_reasoning_effort", "choice", "",
                "Глубина рассуждений модели роли chat (для reasoning-моделей); пусто — не отправляется",
                choices=("", "low", "medium", "high")),
    )
}


def get(key: str) -> Any:
    tunable = TUNABLES[key]

    if (raw := _env(tunable)) is not None:
        return tunable.parse(raw)

    with session() as db:
        row = db.get(Setting, key)

    if row is not None:
        try:
            return tunable.parse(row.value)

        except ValueError as e:
            log.warning("ignoring stored %s: %s", key, e)

    return tunable.parse(tunable.default)


def locked(key: str) -> bool:
    """Set by the environment: the API cannot change it."""
    return _env(TUNABLES[key]) is not None


def describe() -> list[dict[str, Any]]:
    """Every setting with its value, where the value comes from and whether it can change."""
    with session() as db:
        stored = {row.key: row.value for row in db.query(Setting)}

    items = []

    for key, tunable in TUNABLES.items():
        source = "env" if locked(key) else "db" if key in stored else "default"

        items.append({
            "key": key,
            "value": tunable.to_json(get(key)),
            "default": tunable.to_json(tunable.parse(tunable.default)),
            "source": source,
            "locked": source == "env",
            "type": tunable.kind,
            "min": tunable.low,
            "max": tunable.high,
            "choices": list(tunable.choices) if tunable.choices else None,
            "description": tunable.description,
        })

    return items


def check_env() -> None:
    """Fail at start, not on first use, when an environment override is invalid."""
    for tunable in TUNABLES.values():
        if (raw := _env(tunable)) is None:
            continue

        try:
            tunable.parse(raw)

        except ValueError as e:
            raise SettingsError(f"`{tunable.env}`: {e}") from e


def _env(tunable: Tunable) -> str | None:
    # Empty means unset, as everywhere in the .env templates.
    return os.environ.get(tunable.env) or None


def _parse_int(value: Any, low: int | None, high: int | None) -> int:
    if isinstance(value, bool):
        raise ValueError("нужно целое число")

    try:
        number = int(value)

    except (TypeError, ValueError):
        raise ValueError(f"нужно целое число, а не {value!r}") from None

    if isinstance(value, float) and value != number:
        raise ValueError(f"нужно целое число, а не {value!r}")

    if low is not None and number < low:
        raise ValueError(f"не меньше {low}")

    if high is not None and number > high:
        raise ValueError(f"не больше {high}")

    return number


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on"):
        return True

    if isinstance(value, str) and value.strip().lower() in ("0", "false", "no", "off"):
        return False

    raise ValueError(f"нужно да или нет (true/false), а не {value!r}")


def _parse_choice(value: Any, choices: tuple[str, ...]) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else value

    if normalized not in choices:
        raise ValueError(f"одно из {[c for c in choices if c]} или пусто")

    return normalized


def _parse_dir(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("нужен путь к папке")

    # Relative paths (the default, .env) are anchored at the repository root.
    return path_coercion(value.strip())
