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
    NoModelCandidateError,
    NonRetryableProviderError,
    RouterStartupValidationError,
    TransientProviderError,
    UnknownProviderError,
)
from github_auto_maintainer.core.hooks import HookBus
from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.routing_policy import RoutingHint, RoutingPolicy
from github_auto_maintainer.core.settings import AppSettings
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.core.llm_types import LLMHookPayload, LLMMessage, LLMResponse
from github_auto_maintainer.providers.anthropic import AnthropicProvider
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.providers.grok import GrokProvider
from github_auto_maintainer.providers.ollama import OllamaProvider
from github_auto_maintainer.providers.openai import OpenAIProvider

ProviderFactory = Callable[[str], BaseLLMProvider]
ResponseValidator = Callable[[LLMResponse], bool]


@dataclass(slots=True)
class RouterConfig:
    """Runtime defaults read from environment."""

    default_provider: str
    default_model: str

    @classmethod
    def from_env(cls) -> RouterConfig:
        settings = AppSettings()
        return cls(
            default_provider=settings.default_provider.strip().lower(),
            default_model=settings.default_model.strip(),
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
        model_catalog: ModelCatalog | None = None,
        routing_policy: RoutingPolicy | None = None,
    ) -> None:
        self._hook_bus = hook_bus or HookBus()
        self._config = config or RouterConfig.from_env()
        self._retry_config = retry_config or RouterRetryConfig()
        self._provider_factories = provider_factories or self._default_factories()
        self._model_catalog = model_catalog
        self._routing_policy = routing_policy
        if self._routing_policy is None and self._model_catalog is not None:
            self._routing_policy = RoutingPolicy(self._model_catalog)

    def validate_startup(self) -> None:
        """Validate provider defaults and catalog consistency at startup."""

        default_provider = self._config.default_provider.strip().lower()
        if default_provider not in self._provider_factories:
            available = ", ".join(sorted(self._provider_factories))
            raise RouterStartupValidationError(
                "DEFAULT_PROVIDER is not registered: "
                f"'{default_provider}'. Available providers: [{available}]"
            )

        default_model = self._config.default_model.strip()
        catalog = self._get_routing_policy().catalog
        model_found = any(
            descriptor.provider == default_provider and descriptor.model == default_model
            for descriptor in catalog.models
        )
        if not model_found:
            raise RouterStartupValidationError(
                "DEFAULT_MODEL is not present for DEFAULT_PROVIDER in model catalog: "
                f"provider='{default_provider}' model='{default_model}' "
                f"catalog='{catalog.source_path}'"
            )

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

    async def complete_task(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
        task_type: TaskType,
        complexity: TaskComplexity,
        hint: RoutingHint | None = None,
    ) -> LLMResponse:
        """Route by task metadata and execute completion."""

        descriptor = self._get_routing_policy().select(
            task_type=task_type,
            complexity=complexity,
            hint=hint,
        )
        return await self.complete(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            provider=descriptor.provider,
            model=descriptor.model,
        )

    async def complete_with_escalation(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
        task_type: TaskType,
        complexity: TaskComplexity,
        validate: ResponseValidator,
        hint: RoutingHint | None = None,
    ) -> LLMResponse:
        """Attempt tiered completions and escalate deterministically when needed."""

        routing_policy = self._get_routing_policy()
        last_response: LLMResponse | None = None
        no_candidate_errors: list[str] = []
        for tier in routing_policy.escalation_chain(complexity):
            try:
                response = await self.complete_task(
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    task_type=task_type,
                    complexity=tier,
                    hint=hint,
                )
            except NoModelCandidateError as exc:
                no_candidate_errors.append(str(exc))
                continue

            last_response = response
            if validate(response):
                return response

        if last_response is not None:
            return last_response

        joined_errors = "; ".join(no_candidate_errors)
        raise NoModelCandidateError(
            "Escalation failed to find model candidates for "
            f"task='{task_type.value}' starting_complexity='{complexity.value}'. "
            f"Details: {joined_errors}"
        )

    def _get_routing_policy(self) -> RoutingPolicy:
        if self._routing_policy is None:
            if self._model_catalog is None:
                settings = AppSettings()
                self._model_catalog = ModelCatalog.from_yaml(settings.model_catalog_path)
            self._routing_policy = RoutingPolicy(self._model_catalog)
        return self._routing_policy

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
