"""OpenAI LLM provider adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    ProviderConfigurationError,
    TransientProviderError,
)
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.providers.base import BaseLLMProvider


class OpenAIProvider(BaseLLMProvider):
    """OpenAI provider implementation."""

    def __init__(self, model: str, api_key: str | None, base_url: str | None = None) -> None:
        if not api_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is required for OpenAI provider")
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        openai_messages = _to_openai_messages(system=system, messages=messages)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=openai_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ) as exc:
            raise TransientProviderError("Transient OpenAI provider error") from exc
        except openai.OpenAIError as exc:
            raise NonRetryableProviderError("Non-retryable OpenAI provider error") from exc

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        content = _extract_first_content(response)

        return LLMResponse(
            content=content,
            provider="openai",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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


def _extract_first_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""
