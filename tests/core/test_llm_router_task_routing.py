from __future__ import annotations

from collections.abc import Sequence

import pytest
from tenacity import wait_none

from github_auto_maintainer.core.errors import NoModelCandidateError
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig, RouterRetryConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingHint, RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.providers.base import BaseLLMProvider


def _messages() -> list[LLMMessage]:
    return [{"role": "user", "content": "hello"}]


def _catalog(*models: ModelDescriptor) -> ModelCatalog:
    return ModelCatalog(models=tuple(models))


def _descriptor(
    *,
    provider: str,
    model: str,
    cost_tier: int,
    suited_for: set[TaskType],
    context_window: int = 1_000_000,
) -> ModelDescriptor:
    return ModelDescriptor(
        provider=provider,
        model=model,
        litellm_model=f"{provider}/{model}",
        context_window=context_window,
        cost_tier=cost_tier,
        suited_for=frozenset(suited_for),
    )


class RecordingProvider(BaseLLMProvider):
    def __init__(self, provider_name: str, model: str, call_log: list[tuple[str, str]]) -> None:
        self._provider_name = provider_name
        self._model = model
        self._call_log = call_log

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        self._call_log.append((self._provider_name, self._model))
        return LLMResponse(
            content=f"{self._provider_name}:{self._model}",
            provider=self._provider_name,
            model=self._model,
            input_tokens=1,
            output_tokens=1,
        )


def _router(
    *,
    policy: RoutingPolicy | None,
    call_log: list[tuple[str, str]],
) -> LLMRouter:
    def factory(provider: str, model: str, litellm_model: str) -> BaseLLMProvider:
        _ = litellm_model
        return RecordingProvider(provider_name=provider, model=model, call_log=call_log)

    return LLMRouter(
        config=RouterConfig(default_provider="openai", default_model="gpt-5.4-mini"),
        retry_config=RouterRetryConfig(max_attempts=1, wait_strategy=wait_none()),
        provider_factory=factory,
        routing_policy=policy,
    )


@pytest.mark.asyncio
async def test_complete_task_routes_without_explicit_provider_model() -> None:
    catalog = _catalog(
        _descriptor(
            provider="openai",
            model="gpt-5.4-mini",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="ollama",
            model="llama4:scout",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        ),
    )
    calls: list[tuple[str, str]] = []
    router = _router(policy=RoutingPolicy(catalog), call_log=calls)

    response = await router.complete_task(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        hint=RoutingHint(preferred_provider="ollama"),
    )

    assert (response.provider, response.model) == ("ollama", "llama4:scout")
    assert calls == [("ollama", "llama4:scout")]


@pytest.mark.asyncio
async def test_complete_with_escalation_returns_early_when_validate_passes() -> None:
    catalog = _catalog(
        _descriptor(
            provider="openai",
            model="gpt-5.4-mini",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_tier=3,
            suited_for={TaskType.TRIAGE},
        ),
    )
    calls: list[tuple[str, str]] = []
    router = _router(policy=RoutingPolicy(catalog), call_log=calls)

    response = await router.complete_with_escalation(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        validate=lambda llm_response: llm_response.provider == "openai",
    )

    assert response.provider == "openai"
    assert calls == [("openai", "gpt-5.4-mini")]


@pytest.mark.asyncio
async def test_complete_with_escalation_escalates_when_validation_fails() -> None:
    catalog = _catalog(
        _descriptor(
            provider="openai",
            model="gpt-5.4-mini",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_tier=3,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="grok",
            model="grok-4.20-0309-reasoning",
            cost_tier=5,
            suited_for={TaskType.TRIAGE},
        ),
    )
    calls: list[tuple[str, str]] = []
    router = _router(policy=RoutingPolicy(catalog), call_log=calls)

    response = await router.complete_with_escalation(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        validate=lambda llm_response: llm_response.provider == "anthropic",
    )

    assert response.provider == "anthropic"
    assert calls == [
        ("openai", "gpt-5.4-mini"),
        ("anthropic", "claude-sonnet-4-6"),
    ]


@pytest.mark.asyncio
async def test_complete_with_escalation_returns_highest_tier_on_best_effort() -> None:
    catalog = _catalog(
        _descriptor(
            provider="openai",
            model="gpt-5.4-mini",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_tier=3,
            suited_for={TaskType.TRIAGE},
        ),
        _descriptor(
            provider="grok",
            model="grok-4.20-0309-reasoning",
            cost_tier=5,
            suited_for={TaskType.TRIAGE},
        ),
    )
    calls: list[tuple[str, str]] = []
    router = _router(policy=RoutingPolicy(catalog), call_log=calls)

    response = await router.complete_with_escalation(
        system="sys",
        messages=_messages(),
        max_tokens=16,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        validate=lambda _: False,
    )

    assert (response.provider, response.model) == ("grok", "grok-4.20-0309-reasoning")
    assert calls == [
        ("openai", "gpt-5.4-mini"),
        ("anthropic", "claude-sonnet-4-6"),
        ("grok", "grok-4.20-0309-reasoning"),
    ]


@pytest.mark.asyncio
async def test_complete_with_escalation_raises_when_constraints_remove_all_candidates() -> None:
    catalog = _catalog(
        _descriptor(
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_tier=3,
            suited_for={TaskType.PATCH_GENERATION},
        )
    )
    calls: list[tuple[str, str]] = []
    router = _router(policy=RoutingPolicy(catalog), call_log=calls)

    with pytest.raises(NoModelCandidateError):
        await router.complete_with_escalation(
            system="sys",
            messages=_messages(),
            max_tokens=16,
            temperature=0.1,
            task_type=TaskType.PATCH_GENERATION,
            complexity=TaskComplexity.LOW,
            validate=lambda _: True,
            hint=RoutingHint(max_cost_tier=1),
        )

    assert calls == []


@pytest.mark.asyncio
async def test_catalog_is_loaded_once_per_router_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded_paths: list[str] = []
    catalog = _catalog(
        _descriptor(
            provider="openai",
            model="gpt-5.4-mini",
            cost_tier=1,
            suited_for={TaskType.TRIAGE},
        )
    )

    def fake_from_discovery(cls: type[ModelCatalog]) -> ModelCatalog:
        _ = cls
        loaded_paths.append("discovery")
        return catalog

    monkeypatch.setattr(ModelCatalog, "from_discovery", classmethod(fake_from_discovery))
    calls: list[tuple[str, str]] = []
    router = _router(policy=None, call_log=calls)

    await router.complete_task(
        system="sys",
        messages=_messages(),
        max_tokens=8,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
    )
    await router.complete_task(
        system="sys",
        messages=_messages(),
        max_tokens=8,
        temperature=0.1,
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
    )

    assert len(loaded_paths) == 1
