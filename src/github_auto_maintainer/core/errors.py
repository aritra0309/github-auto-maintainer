"""Router and provider error types."""

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
