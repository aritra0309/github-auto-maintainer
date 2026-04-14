"""Skill framework: context, result, and base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import structlog

from github_auto_maintainer.core.actions import ActionRequest
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.events import NormalizedEvent

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class SkillContext:
    """Immutable execution context provided to each skill invocation."""

    event: NormalizedEvent
    github_client: GitHubClient
    router: LLMRouter
    logger: structlog.stdlib.BoundLogger


@dataclass(frozen=True, slots=True)
class SkillResult(Generic[T]):
    """Immutable result of a skill execution."""

    skill_name: str
    event_delivery_id: str
    decision: T
    confidence: float
    reasoning: str
    recommended_actions: tuple[str, ...]
    model_used: str
    task_type_used: TaskType
    complexity_used: TaskComplexity
    elapsed_seconds: float
    planned_actions: tuple[ActionRequest, ...] = ()
    escalation_count: int = 0


class BaseSkill(ABC):
    """Abstract base for all skill implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique skill identifier."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this skill does."""

    @property
    @abstractmethod
    def default_task_type(self) -> TaskType:
        """Default routing task type (may be overridden at execution time)."""

    @property
    @abstractmethod
    def default_complexity(self) -> TaskComplexity:
        """Default routing complexity (may be overridden at execution time)."""

    @abstractmethod
    def handles_event(self, event: NormalizedEvent) -> bool:
        """Return True if this skill should handle the given event."""

    @abstractmethod
    async def execute(self, context: SkillContext) -> SkillResult[Any]:
        """Execute the skill and return a typed result."""
