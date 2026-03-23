"""Grok LLM provider adapter via OpenAI-compatible API."""

from __future__ import annotations

from collections.abc import Sequence

from github_auto_maintainer.core.errors import ProviderConfigurationError
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.providers.openai import OpenAIProvider

DEFAULT_GROK_BASE_URL = "https://api.x.ai/v1"


class GrokProvider(OpenAIProvider):
    """Grok provider backed by OpenAI-compatible client."""

    def __init__(self, model: str, api_key: str | None, base_url: str = DEFAULT_GROK_BASE_URL) -> None:
        if not api_key:
            raise ProviderConfigurationError("GROK_API_KEY is required for Grok provider")
        super().__init__(model=model, api_key=api_key, base_url=base_url)

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        response = await super().complete(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return LLMResponse(
            content=response.content,
            provider="grok",
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
