"""Deterministic policy for task-based model selection and escalation."""

from __future__ import annotations

from dataclasses import dataclass

from github_auto_maintainer.core.errors import NoModelCandidateError
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType

_TIER_ORDER: dict[TaskComplexity, int] = {
    TaskComplexity.LOW: 0,
    TaskComplexity.MEDIUM: 1,
    TaskComplexity.HIGH: 2,
}


@dataclass(frozen=True, slots=True)
class RoutingHint:
    """Optional constraints and preferences for deterministic selection."""

    prefer_local: bool = False
    preferred_provider: str | None = None
    max_cost_tier: TaskComplexity | None = None


class RoutingPolicy:
    """Selects models deterministically from a validated model catalog."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    @property
    def catalog(self) -> ModelCatalog:
        """Return the validated catalog used by this policy."""

        return self._catalog

    def escalation_chain(self, complexity: TaskComplexity) -> tuple[TaskComplexity, ...]:
        """Return deterministic complexity escalation order from starting tier."""

        if complexity == TaskComplexity.LOW:
            return (TaskComplexity.LOW, TaskComplexity.MEDIUM, TaskComplexity.HIGH)
        if complexity == TaskComplexity.MEDIUM:
            return (TaskComplexity.MEDIUM, TaskComplexity.HIGH)
        return (TaskComplexity.HIGH,)

    def select(
        self,
        *,
        task_type: TaskType,
        complexity: TaskComplexity,
        hint: RoutingHint | None = None,
    ) -> ModelDescriptor:
        """Select best model for a task and complexity using deterministic tie-breakers."""

        resolved_hint = hint or RoutingHint()
        preferred_provider = _normalized_provider(resolved_hint.preferred_provider)

        candidates: list[ModelDescriptor] = [
            descriptor
            for descriptor in self._catalog.models
            if task_type in descriptor.suited_for
        ]

        if resolved_hint.max_cost_tier is not None:
            max_rank = _TIER_ORDER[resolved_hint.max_cost_tier]
            candidates = [
                descriptor
                for descriptor in candidates
                if _TIER_ORDER[descriptor.cost_tier] <= max_rank
            ]

        if not candidates:
            raise NoModelCandidateError(
                "No model candidates for "
                f"task='{task_type.value}' complexity='{complexity.value}' "
                f"with hint={resolved_hint}"
            )

        sorted_candidates = sorted(
            candidates,
            key=lambda descriptor: _selection_sort_key(
                descriptor=descriptor,
                requested_complexity=complexity,
                preferred_provider=preferred_provider,
                prefer_local=resolved_hint.prefer_local,
            ),
        )
        return sorted_candidates[0]


def _selection_sort_key(
    *,
    descriptor: ModelDescriptor,
    requested_complexity: TaskComplexity,
    preferred_provider: str | None,
    prefer_local: bool,
) -> tuple[int, int, int, int, int, str, str]:
    requested_rank = _TIER_ORDER[requested_complexity]
    descriptor_rank = _TIER_ORDER[descriptor.cost_tier]
    tier_distance = abs(descriptor_rank - requested_rank)

    preferred_provider_penalty = 0
    if preferred_provider is not None and descriptor.provider != preferred_provider:
        preferred_provider_penalty = 1

    local_penalty = 0
    if prefer_local and descriptor.provider != "ollama":
        local_penalty = 1

    cost_rank = _TIER_ORDER[descriptor.cost_tier]

    return (
        tier_distance,
        preferred_provider_penalty,
        local_penalty,
        cost_rank,
        -descriptor.context_window,
        descriptor.provider,
        descriptor.model,
    )


def _normalized_provider(preferred_provider: str | None) -> str | None:
    if preferred_provider is None:
        return None
    normalized = preferred_provider.strip().lower()
    return normalized or None
