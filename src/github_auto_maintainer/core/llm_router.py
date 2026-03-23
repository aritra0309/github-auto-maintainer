"""LLM router that dispatches calls to registered provider adapters."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    TransientProviderError,
    UnknownProviderError,
)
from github_auto_maintainer.core.hooks import HookBus
from github_auto_maintainer.core.llm_types import LLMHookPayload, LLMMessage, LLMResponse
from github_auto_maintainer.providers.anthropic import AnthropicProvider
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.providers.grok import GrokProvider
from github_auto_maintainer.providers.ollama import OllamaProvider
from github_auto_maintainer.providers.openai import OpenAIProvider

ProviderFactory = Callable[[str], BaseLLMProvider]


@dataclass(slots=True)
class RouterConfig:
    """Runtime defaults read from environment."""

    default_provider: str
    default_model: str

    @classmethod
    def from_env(cls) -> RouterConfig:
        return cls(
            default_provider=os.getenv("DEFAULT_PROVIDER", "openai").strip().lower(),
            default_model=os.getenv("DEFAULT_MODEL", "gpt-4.1-mini").strip(),
        )


@dataclass(slots=True)
class RouterRetryConfig:
    """Retry behavior for transient provider errors."""

    max_attempts: int = 4
    wait_strategy: Any = wait_exponential_jitter(initial=1, max=8)


class LLMRouter:
    """Routes completion requests to the selected provider adapter."""

    def __init__(
        self,
        hook_bus: HookBus | None = None,
        config: RouterConfig | None = None,
        retry_config: RouterRetryConfig | None = None,
        provider_factories: dict[str, ProviderFactory] | None = None,
    ) -> None:
        self._hook_bus = hook_bus or HookBus()
        self._config = config or RouterConfig.from_env()
        self._retry_config = retry_config or RouterRetryConfig()
        self._provider_factories = provider_factories or self._default_factories()

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
        provider: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        selected_provider = (provider or self._config.default_provider).strip().lower()
        selected_model = (model or self._config.default_model).strip()
        prompt_hash = _hash_prompt(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        prompt_payload: LLMHookPayload = {
            "provider": selected_provider,
            "model": selected_model,
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_hash": prompt_hash,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        await self._hook_bus.emit("on_llm_prompt", dict(prompt_payload))

        response = await self._complete_with_retry(
            provider=selected_provider,
            model=selected_model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        response_payload: LLMHookPayload = {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "prompt_hash": prompt_hash,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        await self._hook_bus.emit("on_llm_response", dict(response_payload))
        return response

    async def _complete_with_retry(
        self,
        provider: str,
        model: str,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        retrying = AsyncRetrying(
            retry=retry_if_exception_type(TransientProviderError),
            stop=stop_after_attempt(self._retry_config.max_attempts),
            wait=self._retry_config.wait_strategy,
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                adapter = self._build_provider(provider=provider, model=model)
                return await adapter.complete(
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

        raise NonRetryableProviderError("Provider call failed without producing a response")

    def _build_provider(self, provider: str, model: str) -> BaseLLMProvider:
        factory = self._provider_factories.get(provider)
        if factory is None:
            raise UnknownProviderError(f"No provider registered for '{provider}'")
        return factory(model)

    def _default_factories(self) -> dict[str, ProviderFactory]:
        def anthropic_factory(model: str) -> BaseLLMProvider:
            return AnthropicProvider(model=model, api_key=os.getenv("ANTHROPIC_API_KEY"))

        def openai_factory(model: str) -> BaseLLMProvider:
            return OpenAIProvider(model=model, api_key=os.getenv("OPENAI_API_KEY"))

        def grok_factory(model: str) -> BaseLLMProvider:
            return GrokProvider(model=model, api_key=os.getenv("GROK_API_KEY"))

        def ollama_factory(model: str) -> BaseLLMProvider:
            return OllamaProvider(model=model, base_url=os.getenv("OLLAMA_BASE_URL"))

        return {
            "anthropic": anthropic_factory,
            "openai": openai_factory,
            "grok": grok_factory,
            "ollama": ollama_factory,
        }


def _hash_prompt(
    system: str,
    messages: Sequence[LLMMessage],
    max_tokens: int,
    temperature: float,
) -> str:
    payload = {
        "system": system,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
