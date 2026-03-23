"""Ollama LLM provider adapter via local OpenAI-compatible API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import httpx
from openai.types.chat import ChatCompletionMessageParam

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    ProviderConfigurationError,
    TransientProviderError,
)
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.providers.base import BaseLLMProvider


class OllamaProvider(BaseLLMProvider):
    """Ollama provider implementation using httpx."""

    def __init__(self, model: str, base_url: str | None) -> None:
        if not base_url:
            raise ProviderConfigurationError("OLLAMA_BASE_URL is required for Ollama provider")
        self._model = model
        self._base_url = base_url.rstrip("/")

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": _to_openai_messages(system=system, messages=messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        url = f"{self._base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            raise TransientProviderError("Transient Ollama connection error") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
                raise TransientProviderError("Transient Ollama server error") from exc
            raise NonRetryableProviderError("Non-retryable Ollama server error") from exc
        except httpx.HTTPError as exc:
            raise NonRetryableProviderError("Non-retryable Ollama HTTP error") from exc

        data = response.json()
        usage = data.get("usage", {})
        return LLMResponse(
            content=_extract_content(data),
            provider="ollama",
            model=self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
        )


def _to_openai_messages(system: str, messages: Sequence[LLMMessage]) -> list[ChatCompletionMessageParam]:
    normalized: list[ChatCompletionMessageParam] = []
    if system:
        normalized.append(cast(ChatCompletionMessageParam, {"role": "system", "content": system}))
    for message in messages:
        normalized.append(
            cast(
                ChatCompletionMessageParam,
                {"role": message["role"], "content": message["content"]},
            )
        )
    return normalized


def _extract_content(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message", {})
    content = message.get("content", "")
    return content if isinstance(content, str) else ""
