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

        missing = [m for m in required_models if m not in models]
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
    ) -> Any:
        """Ask for JSON matching `schema`; validation errors go back to the model.

        JSON-schema mode is tried first. Some models (gpt-oss on Ollama) answer it with
        empty content, so the next attempt falls back to plain JSON mode with the
        schema spelled out in the prompt."""
        conversation = list(messages)
        use_schema_mode = True
        last_error = "no attempts made"

        for attempt in range(1, attempts + 1):
            if use_schema_mode:
                response_format = {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}
            else:
                response_format = {"type": "json_object"}

            try:
                message = await self.chat(conversation, model=model, response_format=response_format)

            except ApiError as e:
                if use_schema_mode and e.status_code in (400, 422, 500):
                    log.warning("json_schema mode rejected (%s), retrying in json mode", e.message)
                    use_schema_mode = False
                    conversation = _with_schema_hint(conversation, schema)
                    continue
                raise

            content = message["content"]

            if not content:
                last_error = "empty content"
                if use_schema_mode:
                    use_schema_mode = False
                    conversation = _with_schema_hint(conversation, schema)
                continue

            try:
                data = extract_json(content)
                return validate(data) if validate else data

            except (ValueError, TypeError, KeyError) as e:
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
            details={"last_error": truncate(last_error, 2000), "attempts": attempts},
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


def _with_schema_hint(messages: list[dict[str, Any]], schema: dict[str, Any]) -> list[dict[str, Any]]:
    hint = (
        "Отвечай строго одним JSON-объектом без пояснений, соответствующим JSON Schema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    return [{"role": "system", "content": hint}, *messages]
