"""LLM router that dispatches calls to the LiteLLM provider adapter."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from github_auto_maintainer.core.errors import (
    NoModelCandidateError,
    NonRetryableProviderError,
    RouterStartupValidationError,
    TransientProviderError,
)
from github_auto_maintainer.core.hooks import HookBus
from github_auto_maintainer.core.llm_types import LLMHookPayload, LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.routing_policy import RoutingHint, RoutingPolicy
from github_auto_maintainer.core.settings import AppSettings
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.providers.litellm_provider import LiteLLMProvider

ProviderFactory = Callable[[str, str, str], BaseLLMProvider]
ResponseValidator = Callable[[LLMResponse], bool]


@dataclass(slots=True)
class RouterConfig:
    """Runtime defaults read from environment.

    Both fields are optional. When set, they act as preference hints for
    routing (preferred provider / model). When empty, the system picks the
    best available model automatically via auto-discovery.
    """

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
    """Routes completion requests to the LiteLLM provider adapter."""

    def __init__(
        self,
        hook_bus: HookBus | None = None,
        config: RouterConfig | None = None,
        retry_config: RouterRetryConfig | None = None,
        provider_factory: ProviderFactory | None = None,
        model_catalog: ModelCatalog | None = None,
        routing_policy: RoutingPolicy | None = None,
    ) -> None:
        self._hook_bus = hook_bus or HookBus()
        self._config = config or RouterConfig.from_env()
        self._retry_config = retry_config or RouterRetryConfig()
        self._provider_factory = provider_factory or _default_litellm_factory
        self._model_catalog = model_catalog
        self._routing_policy = routing_policy
        if self._routing_policy is None and self._model_catalog is not None:
            self._routing_policy = RoutingPolicy(self._model_catalog)

    def validate_startup(self) -> None:
        """Validate that at least one provider was detected and models are available.

        If DEFAULT_PROVIDER and DEFAULT_MODEL are set, verifies they exist in
        the catalog. If not set (empty), validation passes as long as the
        catalog has at least one model.
        """

        catalog = self._get_routing_policy().catalog

        default_provider = self._config.default_provider.strip().lower()
        default_model = self._config.default_model.strip()

        # If defaults are set, verify they exist in the catalog
        if default_provider and default_model:
            model_found = any(
                descriptor.provider == default_provider
                and descriptor.model == default_model
                for descriptor in catalog.models
            )
            if not model_found:
                raise RouterStartupValidationError(
                    "DEFAULT_MODEL is not present for DEFAULT_PROVIDER in model catalog: "
                    f"provider='{default_provider}' model='{default_model}'"
                )

        # Verify at least one model exists (discovery already enforces this,
        # but belt-and-suspenders)
        if not catalog.models:
            raise RouterStartupValidationError(
                "Model catalog is empty — no models were discovered. "
                "Set at least one LLM provider API key."
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
        selected_provider = provider or self._config.default_provider or None
        selected_model = model or self._config.default_model or None

        # If no explicit provider/model, use the first model from the catalog
        if not selected_provider or not selected_model:
            catalog = self._get_routing_policy().catalog
            first = catalog.models[0]
            selected_provider = selected_provider or first.provider
            selected_model = selected_model or first.model

        selected_provider = selected_provider.strip().lower()
        selected_model = selected_model.strip()

        # Look up the litellm_model string from the catalog
        litellm_model = self._resolve_litellm_model(selected_provider, selected_model)

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
            litellm_model=litellm_model,
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
        attempts = 0
        for target_tier in routing_policy.escalation_chain(complexity):
            try:
                descriptor = routing_policy.select_for_tier(
                    task_type=task_type,
                    target_tier=target_tier,
                    hint=hint,
                )
            except NoModelCandidateError as exc:
                no_candidate_errors.append(str(exc))
                continue

            attempts += 1

            try:
                response = await self.complete(
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    provider=descriptor.provider,
                    model=descriptor.model,
                )
            except NoModelCandidateError as exc:
                no_candidate_errors.append(str(exc))
                continue

            last_response = response
            if validate(response):
                escalation_count = attempts - 1
                return replace(response, escalation_count=escalation_count)

        if last_response is not None:
            escalation_count = attempts - 1
            return replace(last_response, escalation_count=escalation_count)

        joined_errors = "; ".join(no_candidate_errors)
        raise NoModelCandidateError(
            "Escalation failed to find model candidates for "
            f"task='{task_type.value}' starting_complexity='{complexity.value}'. "
            f"Details: {joined_errors}"
        )

    def _resolve_litellm_model(self, provider: str, model: str) -> str:
        """Look up the litellm_model string from the catalog.

        Falls back to ``"{provider}/{model}"`` if not found in the catalog
        (e.g. when using direct ``complete()`` calls with ad-hoc models).
        Does **not** force catalog loading — if no catalog is available yet,
        returns the fallback immediately.
        """
        if self._routing_policy is not None:
            catalog = self._routing_policy.catalog
            for descriptor in catalog.models:
                if descriptor.provider == provider and descriptor.model == model:
                    return descriptor.litellm_model
        elif self._model_catalog is not None:
            for descriptor in self._model_catalog.models:
                if descriptor.provider == provider and descriptor.model == model:
                    return descriptor.litellm_model
        return f"{provider}/{model}"

    def _get_routing_policy(self) -> RoutingPolicy:
        if self._routing_policy is None:
            if self._model_catalog is None:
                self._model_catalog = ModelCatalog.from_discovery()
            self._routing_policy = RoutingPolicy(self._model_catalog)
        return self._routing_policy

    async def _complete_with_retry(
        self,
        provider: str,
        model: str,
        litellm_model: str,
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
                adapter = self._build_provider(
                    provider=provider,
                    model=model,
                    litellm_model=litellm_model,
                )
                return await adapter.complete(
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

        raise NonRetryableProviderError("Provider call failed without producing a response")

    def _build_provider(
        self, provider: str, model: str, litellm_model: str
    ) -> BaseLLMProvider:
        return self._provider_factory(provider, model, litellm_model)


def _default_litellm_factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
    """Default factory that creates a LiteLLMProvider instance."""
    return LiteLLMProvider(litellm_model=litellm_model, provider=provider, model=model)


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
