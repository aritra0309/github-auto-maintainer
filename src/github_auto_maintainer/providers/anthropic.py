"""Anthropic LLM provider adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, cast

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    ProviderConfigurationError,
    TransientProviderError,
)
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.providers.base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """Anthropic provider implementation."""

    def __init__(self, model: str, api_key: str | None) -> None:
        if not api_key:
            raise ProviderConfigurationError("ANTHROPIC_API_KEY is required for Anthropic provider")
        self._model = model
        self._client = AsyncAnthropic(api_key=api_key)

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        anthropic_messages = _to_anthropic_messages(messages)
        full_system = _merge_system(system=system, messages=messages)

        try:
            response = await self._client.messages.create(
                model=self._model,
                system=full_system,
                messages=anthropic_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.InternalServerError,
        ) as exc:
            raise TransientProviderError("Transient Anthropic provider error") from exc
        except anthropic.AnthropicError as exc:
            raise NonRetryableProviderError("Non-retryable Anthropic provider error") from exc

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        return LLMResponse(
            content=_extract_text_blocks(response.content),
            provider="anthropic",
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _merge_system(system: str, messages: Sequence[LLMMessage]) -> str:
    inline_system = [message["content"] for message in messages if message["role"] == "system"]
    if not inline_system:
        return system
    parts = [system, *inline_system] if system else inline_system
    return "\n\n".join(part for part in parts if part)


def _to_anthropic_messages(messages: Sequence[LLMMessage]) -> list[MessageParam]:
    normalized: list[MessageParam] = []
    for message in messages:
        role = message["role"]
        if role not in {"user", "assistant"}:
            continue
        normalized.append(
            {
                "role": cast(Literal["user", "assistant"], role),
                "content": message["content"],
            }
        )
    return normalized


def _extract_text_blocks(blocks: Sequence[Any]) -> str:
    content_parts: list[str] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "")
            if isinstance(text, str):
                content_parts.append(text)
            continue
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str):
                content_parts.append(text)
    return "".join(content_parts)
