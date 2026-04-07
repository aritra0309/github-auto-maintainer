"""Router, provider, and skill error types."""

from __future__ import annotations


class LLMRouterError(Exception):
    """Base error for router-related failures."""


class UnknownProviderError(LLMRouterError):
    """Raised when no provider adapter is registered for a provider key."""


class ProviderConfigurationError(LLMRouterError):
    """Raised when required provider configuration is missing."""


class TransientProviderError(LLMRouterError):
    """Raised for retryable network/rate-limit style failures."""


class NonRetryableProviderError(LLMRouterError):
    """Raised for non-retryable provider failures."""


class ModelCatalogError(LLMRouterError):
    """Base error for model catalog failures."""


class ModelCatalogLoadError(ModelCatalogError):
    """Raised when model catalog file cannot be loaded."""


class ModelCatalogValidationError(ModelCatalogError):
    """Raised when model catalog structure or values are invalid."""


class RoutingPolicyError(LLMRouterError):
    """Base error for deterministic routing policy failures."""


class NoModelCandidateError(RoutingPolicyError):
    """Raised when no catalog model matches routing constraints."""


class RouterStartupValidationError(LLMRouterError):
    """Raised when router startup validation detects invalid configuration."""


# ── Skill errors ──────────────────────────────────────────────────────


class SkillError(Exception):
    """Base error for skill execution failures."""


class SkillExecutionError(SkillError):
    """Raised when a skill fails during execution (e.g. GitHub client failure)."""


class SkillResponseParsingError(SkillError):
    """Raised when LLM response cannot be parsed into a typed decision."""
