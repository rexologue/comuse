"""LLMClient.structured: mode fallback costs no attempts, validation errors do."""

from __future__ import annotations

import pytest

from common.clients.llm_client import LLMClient
from common.errors import ApiError


class ScriptedLLM(LLMClient):
    """Replies are scripted per response_format type (None = plain mode)."""

    def __init__(self, replies: dict[str | None, list[str]]):
        super().__init__(base_url="http://llm", api_key="", timeout=1)

        self.replies = {key: list(values) for key, values in replies.items()}
        self.modes: list[str | None] = []
        self.sent: list[list[dict]] = []


    async def chat(self, messages, *, model, response_format=None, **_):
        mode = response_format["type"] if response_format else None
        self.modes.append(mode)
        self.sent.append(messages)

        return {"content": self.replies[mode].pop(0)}


SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}}


def _positive(data):
    if data["n"] <= 0:
        raise ValueError("n должно быть больше нуля")

    return data


async def test_empty_and_prose_replies_fall_through_to_plain_mode():
    llm = ScriptedLLM({"json_schema": [""], "json_object": ["Конечно! Вот ответ."], None: ['{"n": 3}']})

    result = await llm.structured([{"role": "user", "content": "?"}], model="m", schema=SCHEMA, attempts=1)

    assert result == {"n": 3}
    assert llm.modes == ["json_schema", "json_object", None]


async def test_working_mode_is_remembered_per_model():
    llm = ScriptedLLM({"json_schema": [""], "json_object": [""], None: ['{"n": 1}', '{"n": 2}']})

    await llm.structured([], model="m", schema=SCHEMA)
    await llm.structured([], model="m", schema=SCHEMA)

    assert llm.modes == ["json_schema", "json_object", None, None]


async def test_validation_errors_go_back_and_count_attempts():
    llm = ScriptedLLM({"json_schema": ['{"n": 0}', '{"n": 5}']})

    result = await llm.structured([], model="m", schema=SCHEMA, validate=_positive, attempts=2)

    assert result == {"n": 5}


async def test_gives_up_after_attempts():
    llm = ScriptedLLM({"json_schema": ['{"n": 0}', '{"n": -1}']})

    with pytest.raises(ApiError):
        await llm.structured([], model="m", schema=SCHEMA, validate=_positive, attempts=2)


def test_recursive_schema_detection():
    from common.clients.llm_client import is_recursive
    from common.schemas.task import TaskSpec

    assert is_recursive(TaskSpec.model_json_schema(by_alias=True))
    assert is_recursive({"type": "object", "properties": {"child": {"$ref": "#"}}})
    assert not is_recursive(SCHEMA)
    assert not is_recursive({"$defs": {"A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}}, "B": {"type": "string"}}})


async def test_recursive_schema_skips_json_schema_mode():
    from common.schemas.task import TaskSpec

    llm = ScriptedLLM({"json_object": ['{"n": 1}']})

    await llm.structured([], model="m", schema={**TaskSpec.model_json_schema(by_alias=True)})

    assert llm.modes == ["json_object"]


async def test_schema_hint_keeps_a_single_leading_system_message():
    llm = ScriptedLLM({"json_schema": [""], "json_object": ['{"n": 1}']})
    messages = [{"role": "system", "content": "Правила"}, {"role": "user", "content": "?"}]

    await llm.structured(messages, model="m", schema=SCHEMA)

    sent = llm.sent[-1]
    assert [m["role"] for m in sent] == ["system", "user"]
    assert sent[0]["content"].startswith("Правила") and "JSON Schema" in sent[0]["content"]


async def test_schema_hint_adds_a_system_message_when_there_is_none():
    llm = ScriptedLLM({"json_schema": [""], "json_object": ['{"n": 1}']})

    await llm.structured([{"role": "user", "content": "?"}], model="m", schema=SCHEMA)

    assert [m["role"] for m in llm.sent[-1]] == ["system", "user"]
