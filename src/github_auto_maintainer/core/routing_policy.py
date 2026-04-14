"""Deterministic policy for task-based model selection and escalation."""

from __future__ import annotations

from dataclasses import dataclass

from github_auto_maintainer.core.errors import NoModelCandidateError
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.task_types import TARGET_TIER, TaskComplexity, TaskType


@dataclass(frozen=True, slots=True)
class RoutingHint:
    """Optional constraints and preferences for deterministic selection."""

    prefer_local: bool = False
    preferred_provider: str | None = None
    max_cost_tier: int | None = None


class RoutingPolicy:
    """Selects models deterministically from a validated model catalog."""

    def __init__(self, catalog: ModelCatalog) -> None:
        self._catalog = catalog

    @property
    def catalog(self) -> ModelCatalog:
        """Return the validated catalog used by this policy."""

        return self._catalog

    def escalation_chain(self, complexity: TaskComplexity) -> tuple[int, ...]:
        """Return deterministic target tier escalation order from starting complexity."""

        if complexity == TaskComplexity.LOW:
            return (1, 3, 5)
        if complexity == TaskComplexity.MEDIUM:
            return (3, 5)
        return (5,)

    def select(
        self,
        *,
        task_type: TaskType,
        complexity: TaskComplexity,
        hint: RoutingHint | None = None,
    ) -> ModelDescriptor:
        """Select best model for a task and complexity using deterministic tie-breakers."""

        target_tier = TARGET_TIER[complexity]
        return self.select_for_tier(
            task_type=task_type,
            target_tier=target_tier,
            hint=hint,
        )

    def select_for_tier(
        self,
        *,
        task_type: TaskType,
        target_tier: int,
        hint: RoutingHint | None = None,
    ) -> ModelDescriptor:
        """Select best model for a task at a specific target tier.

        This is the core selection method. ``select()`` delegates here after
        mapping ``TaskComplexity`` to an int tier. The escalation loop in
        ``LLMRouter`` calls this directly with int tiers from
        ``escalation_chain()``.
        """

        resolved_hint = hint or RoutingHint()
        preferred_provider = _normalized_provider(resolved_hint.preferred_provider)

        candidates: list[ModelDescriptor] = [
            descriptor
            for descriptor in self._catalog.models
            if task_type in descriptor.suited_for
        ]

        if resolved_hint.max_cost_tier is not None:
            candidates = [
                descriptor
                for descriptor in candidates
                if descriptor.cost_tier <= resolved_hint.max_cost_tier
            ]

        if not candidates:
            raise NoModelCandidateError(
                "No model candidates for "
                f"task='{task_type.value}' target_tier={target_tier} "
                f"with hint={resolved_hint}"
            )

        sorted_candidates = sorted(
            candidates,
            key=lambda descriptor: _selection_sort_key(
                descriptor=descriptor,
                target_tier=target_tier,
                preferred_provider=preferred_provider,
                prefer_local=resolved_hint.prefer_local,
            ),
        )
        return sorted_candidates[0]


def _selection_sort_key(
    *,
    descriptor: ModelDescriptor,
    target_tier: int,
    preferred_provider: str | None,
    prefer_local: bool,
) -> tuple[int, int, int, int, int, str, str]:
    tier_distance = abs(descriptor.cost_tier - target_tier)

    preferred_provider_penalty = 0
    if preferred_provider is not None and descriptor.provider != preferred_provider:
        preferred_provider_penalty = 1

    local_penalty = 0
    if prefer_local and descriptor.provider != "ollama":
        local_penalty = 1

    return (
        tier_distance,
        preferred_provider_penalty,
        local_penalty,
        descriptor.cost_tier,
        -descriptor.context_window,
        descriptor.provider,
        descriptor.model,
    )


def _normalized_provider(preferred_provider: str | None) -> str | None:
    if preferred_provider is None:
        return None
    normalized = preferred_provider.strip().lower()
    return normalized or None
