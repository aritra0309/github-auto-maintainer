"""Typed task and complexity enums for deterministic model routing."""

from __future__ import annotations

from enum import StrEnum


class TaskComplexity(StrEnum):
    """Complexity tiers used for model routing and escalation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskType(StrEnum):
    """Supported task intents for catalog-based routing."""

    TRIAGE = "triage"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    PATCH_GENERATION = "patch_generation"
    DEEP_REVIEW = "deep_review"
    ARCHITECTURE = "architecture"
    AGENTIC_WORKFLOWS = "agentic_workflows"
    COMPLEX_REASONING = "complex_reasoning"
    LONG_HORIZON_TASKS = "long_horizon_tasks"
    ENTERPRISE_AGENTS = "enterprise_agents"
    QUICK_FIXES = "quick_fixes"
    CODING_SUBAGENTS = "coding_subagents"
    REAL_TIME_INVESTIGATION = "real_time_investigation"
    INVESTIGATION = "investigation"
    CHAIN_OF_THOUGHT_TASKS = "chain_of_thought_tasks"
    ORCHESTRATION = "orchestration"
    MULTI_STEP_TASKS = "multi_step_tasks"
    OFFLINE_TRIAGE = "offline_triage"
    LOCAL_DEV = "local_dev"
    MULTIMODAL_TASKS = "multimodal_tasks"
    OFFLINE_PATCH_GENERATION = "offline_patch_generation"
    CODE_REVIEW = "code_review"
    REFACTORING = "refactoring"


# Maps skill-facing TaskComplexity to the integer target tier used by the routing policy.
# Tiers range from 0 (free/local) to 5 (most expensive frontier models).
TARGET_TIER: dict[TaskComplexity, int] = {
    TaskComplexity.LOW: 1,
    TaskComplexity.MEDIUM: 3,
    TaskComplexity.HIGH: 5,
}
