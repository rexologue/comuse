from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import httpx

from common.errors import ApiError
from common.settings import LLMSettings
from common.utils import error_body, extract_json, strip_think, truncate


log = logging.getLogger("llm")

# Fixed linear backoff between transient-connection retries (seconds * attempt).
_RETRY_BACKOFF_SEC = 1.0

# Structured output, from strict to loose; see LLMClient.structured.
_MODES = ("json_schema", "json_object", "plain")
_LAST_MODE = len(_MODES) - 1


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float | httpx.Timeout,
        no_proxy: bool = True,
        max_retries: int = 2,
    ):

        self.base_url    = base_url
        self.api_key     = api_key
        self.timeout     = timeout
        self.max_retries = max_retries

        # `--noproxy '*'` equivalent: ignore HTTP(S)_PROXY from the environment, the
        # team's LLM server is reachable directly and corporate proxies break it.
        self._client = httpx.AsyncClient(timeout=timeout, trust_env=not no_proxy)

        # Index into _MODES of the structured-output mode that last worked per model.
        self._structured_mode: dict[str, int] = {}


    @property
    def route(self) -> str:
        return f"{self.base_url}/chat/completions"


    @property
    def models_route(self) -> str:
        return f"{self.base_url}/models"


    @property
    def embeddings_route(self) -> str:
        return f"{self.base_url}/embeddings"


    @property
    def auth_header(self) -> dict:
        # The team server needs no key; sending a dummy Authorization is avoided.
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


    @classmethod
    def from_settings(cls, ls: LLMSettings) -> LLMClient:
        return cls(
            base_url=ls.base_url,
            api_key=ls.api_key,
            timeout=ls.timeout,
            no_proxy=ls.no_proxy,
            max_retries=ls.max_retries,
        )


    async def close(self) -> None:
        await self._client.aclose()


    async def check_connection(self, required_models: list[str]) -> None:
        """Verify the server answers and serves the configured models."""
        data = await self._send("GET", self.models_route)

        try:
            models = [model["id"] for model in data["data"]]

        except (KeyError, TypeError) as e:
            raise ApiError(
                status_code=422,
                message=f"Unexpected response structure from {self.models_route}",
                details={"base_message": str(e)},
            ) from e

        # Ollama lists `bge-m3:latest` but serves plain `bge-m3` too.
        served = set(models) | {m.removesuffix(":latest") for m in models}
        missing = [m for m in required_models if m not in served]
        if missing:
            raise ApiError(
                status_code=404,
                message=f"Models {missing} are not served at {self.base_url}",
                details={"models": models},
            )


    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """One non-streaming completion; returns the assistant message with
        reasoning blocks stripped from `content`."""
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}

        if tools:
            body["tools"] = tools

        if response_format is not None:
            body["response_format"] = response_format

        if temperature is not None:
            body["temperature"] = temperature

        # Reasoning models (gpt-oss) trade quality for latency: low / medium / high.
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        data = await self._send("POST", self.route, json_body=body)

        try:
            message = data["choices"][0]["message"]

        except (KeyError, IndexError, TypeError) as e:
            raise ApiError(
                status_code=502,
                message="Unexpected chat completion structure",
                details={"response": truncate(data, 1000)},
            ) from e

        message["content"] = strip_think(message.get("content") or "")

        return message


    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = await self._send("POST", self.embeddings_route, json_body={"model": model, "input": texts})

        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in items]

        except (KeyError, TypeError) as e:
            raise ApiError(
                status_code=502,
                message="Unexpected embeddings structure",
                details={"response": truncate(data, 1000)},
            ) from e


    async def structured(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        schema: dict[str, Any],
        name: str = "result",
        validate: Callable[[Any], Any] | None = None,
        attempts: int = 3,
        reasoning_effort: str | None = None,
    ) -> Any:
        """Ask for JSON matching `schema`; validation errors go back to the model.

        Output modes are tried from strict to loose: json_schema, json_object, then
        plain text with the schema in the prompt. A server or model that rejects a mode
        or answers it with empty content (gpt-oss on Ollama does both) drops to the
        next one; the mode that worked is remembered per model. Falling back costs no
        attempt — `attempts` counts only replies the model got wrong."""
        mode = self._structured_mode.get(model, 0)
        conversation = list(messages)
        last_error = "no attempts made"
        attempt = 0

        while attempt < attempts:
            try:
                message = await self.chat(
                    _with_schema_hint(conversation, schema) if mode > 0 else conversation,
                    model=model,
                    response_format=_response_format(mode, name, schema),
                    reasoning_effort=reasoning_effort,
                )
                content = message["content"]

            except ApiError as e:
                if mode < _LAST_MODE and e.status_code in (400, 422, 500, 502):
                    log.warning("structured mode %s failed for %s (%s), trying the next", _MODES[mode], model, e.message)
                    mode += 1
                    continue
                raise

            if not content:
                if mode < _LAST_MODE:
                    log.warning("structured mode %s gave empty content for %s, trying the next", _MODES[mode], model)
                    mode += 1
                    continue

                attempt += 1
                last_error = "empty content"
                continue

            self._structured_mode[model] = mode

            try:
                data = extract_json(content)
                return validate(data) if validate else data

            except (ValueError, TypeError, KeyError) as e:
                attempt += 1
                last_error = str(e)
                log.info("structured attempt %d rejected: %s", attempt, truncate(last_error, 500))
                conversation = conversation + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Ответ не прошёл проверку:\n"
                            f"{truncate(last_error, 3000)}\n"
                            "Исправь ошибки и верни только исправленный JSON целиком."
                        ),
                    },
                ]

        raise ApiError(
            status_code=502,
            message="LLM did not return valid structured output",
            details={"last_error": truncate(last_error, 2000), "attempts": attempts, "mode": _MODES[mode]},
        )


    async def _send(self, method: str, url: str, json_body: dict[str, Any] | None = None) -> Any:
        """HTTP call with linear-backoff retries on connection errors (not on 4xx/5xx)."""
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, url, json=json_body, headers=self.auth_header)
                break

            except httpx.RequestError as e:
                if attempt >= self.max_retries:
                    raise ApiError(
                        status_code=503,
                        message=f"Cannot reach LLM at {url}",
                        details={"error": repr(e)},
                    ) from e

                await asyncio.sleep(_RETRY_BACKOFF_SEC * (attempt + 1))

        if response.status_code >= 400:
            raise ApiError(
                status_code=502 if response.status_code >= 500 else response.status_code,
                message=f"LLM responded with HTTP {response.status_code}",
                details={"body": error_body(response)},
            )

        try:
            return response.json()

        except ValueError as e:
            raise ApiError(
                status_code=502,
                message=f"Cannot decode LLM response from {url}",
                details={"text": truncate(response.text, 1000)},
            ) from e


def _response_format(mode: int, name: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    if _MODES[mode] == "json_schema":
        return {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}

    if _MODES[mode] == "json_object":
        return {"type": "json_object"}

    return None


def _with_schema_hint(messages: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    hint = (
        "Отвечай строго одним JSON-объектом без пояснений, соответствующим JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    return [{"role": "system", "content": hint}, *messages]
