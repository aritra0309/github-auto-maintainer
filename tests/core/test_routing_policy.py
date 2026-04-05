from __future__ import annotations

from pathlib import Path

import pytest

from github_auto_maintainer.core.errors import NoModelCandidateError
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingHint, RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType


def _catalog(*models: ModelDescriptor) -> ModelCatalog:
    return ModelCatalog(models=tuple(models), source_path=Path("/tmp/test-models.yaml"))


def _descriptor(
    *,
    provider: str,
    model: str,
    context_window: int,
    cost_tier: TaskComplexity,
    suited_for: set[TaskType] | None = None,
) -> ModelDescriptor:
    return ModelDescriptor(
        provider=provider,
        model=model,
        context_window=context_window,
        cost_tier=cost_tier,
        suited_for=frozenset(suited_for or {TaskType.TRIAGE}),
    )


def test_select_is_deterministic_for_task_and_complexity() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="gpt-5.4-mini",
                context_window=1_000_000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="anthropic",
                model="claude-sonnet-4-6",
                context_window=1_000_000,
                cost_tier=TaskComplexity.MEDIUM,
            ),
        )
    )

    first = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.LOW)
    second = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.LOW)

    assert (first.provider, first.model) == ("openai", "gpt-5.4-mini")
    assert (second.provider, second.model) == ("openai", "gpt-5.4-mini")


def test_select_uses_lexical_tiebreak_for_equal_candidates() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="z-model",
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="openai",
                model="a-model",
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
            ),
        )
    )

    selected = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.LOW)

    assert (selected.provider, selected.model) == ("openai", "a-model")


def test_select_prefers_larger_context_window_before_lexical_tiebreak() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="a-model",
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="openai",
                model="z-model",
                context_window=2000,
                cost_tier=TaskComplexity.LOW,
            ),
        )
    )

    selected = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.LOW)

    assert (selected.provider, selected.model) == ("openai", "z-model")


def test_select_prefers_lower_cost_tier_before_context_window() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="low-cost-smaller-context",
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="openai",
                model="high-cost-larger-context",
                context_window=100000,
                cost_tier=TaskComplexity.HIGH,
            ),
        )
    )

    selected = policy.select(task_type=TaskType.TRIAGE, complexity=TaskComplexity.MEDIUM)

    assert (selected.provider, selected.model) == ("openai", "low-cost-smaller-context")


def test_select_prefers_local_when_other_factors_are_comparable() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="gpt-5.4-mini",
                context_window=2000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="ollama",
                model="llama4:scout",
                context_window=1000,
                cost_tier=TaskComplexity.LOW,
            ),
        )
    )

    selected = policy.select(
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        hint=RoutingHint(prefer_local=True),
    )

    assert (selected.provider, selected.model) == ("ollama", "llama4:scout")


def test_select_prefers_requested_provider() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="gpt-5.4-mini",
                context_window=2000,
                cost_tier=TaskComplexity.LOW,
            ),
            _descriptor(
                provider="grok",
                model="grok-4-1-fast-non-reasoning",
                context_window=2000,
                cost_tier=TaskComplexity.LOW,
            ),
        )
    )

    selected = policy.select(
        task_type=TaskType.TRIAGE,
        complexity=TaskComplexity.LOW,
        hint=RoutingHint(preferred_provider="grok"),
    )

    assert selected.provider == "grok"


def test_select_applies_max_cost_tier_filter() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="anthropic",
                model="claude-sonnet-4-6",
                context_window=1_000_000,
                cost_tier=TaskComplexity.MEDIUM,
                suited_for={TaskType.PATCH_GENERATION},
            ),
            _descriptor(
                provider="openai",
                model="gpt-5.4-mini",
                context_window=1_000_000,
                cost_tier=TaskComplexity.LOW,
                suited_for={TaskType.PATCH_GENERATION},
            ),
        )
    )

    selected = policy.select(
        task_type=TaskType.PATCH_GENERATION,
        complexity=TaskComplexity.HIGH,
        hint=RoutingHint(max_cost_tier=TaskComplexity.LOW),
    )

    assert (selected.provider, selected.model) == ("openai", "gpt-5.4-mini")


def test_select_raises_when_no_candidate_after_filters() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="anthropic",
                model="claude-opus-4-6",
                context_window=1_000_000,
                cost_tier=TaskComplexity.HIGH,
                suited_for={TaskType.PATCH_GENERATION},
            )
        )
    )

    with pytest.raises(NoModelCandidateError):
        policy.select(
            task_type=TaskType.PATCH_GENERATION,
            complexity=TaskComplexity.LOW,
            hint=RoutingHint(max_cost_tier=TaskComplexity.LOW),
        )


def test_escalation_chain_order_is_deterministic() -> None:
    policy = RoutingPolicy(
        _catalog(
            _descriptor(
                provider="openai",
                model="gpt-5.4-mini",
                context_window=1_000_000,
                cost_tier=TaskComplexity.LOW,
            )
        )
    )

    assert policy.escalation_chain(TaskComplexity.LOW) == (
        TaskComplexity.LOW,
        TaskComplexity.MEDIUM,
        TaskComplexity.HIGH,
    )
    assert policy.escalation_chain(TaskComplexity.MEDIUM) == (
        TaskComplexity.MEDIUM,
        TaskComplexity.HIGH,
    )
    assert policy.escalation_chain(TaskComplexity.HIGH) == (TaskComplexity.HIGH,)
