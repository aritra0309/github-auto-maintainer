"""Tests for LiteLLMProvider — unified LLM adapter backed by LiteLLM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, patch

import litellm
import pytest

# LiteLLM exception aliases — litellm doesn't export these via __all__ so mypy
# can't see them at static analysis time, but they exist at runtime.
_RateLimitError: type[Exception] = litellm.RateLimitError  # type: ignore[attr-defined]
_ServiceUnavailableError: type[Exception] = litellm.ServiceUnavailableError  # type: ignore[attr-defined]
_Timeout: type[Exception] = litellm.Timeout  # type: ignore[attr-defined]
_APIConnectionError: type[Exception] = litellm.APIConnectionError  # type: ignore[attr-defined]
_AuthenticationError: type[Exception] = litellm.AuthenticationError  # type: ignore[attr-defined]
_NotFoundError: type[Exception] = litellm.NotFoundError  # type: ignore[attr-defined]
_BadRequestError: type[Exception] = litellm.BadRequestError  # type: ignore[attr-defined]
_ContentPolicyViolationError: type[Exception] = litellm.ContentPolicyViolationError  # type: ignore[attr-defined]
_ContextWindowExceededError: type[Exception] = litellm.ContextWindowExceededError  # type: ignore[attr-defined]
_APIError: type[Exception] = litellm.APIError  # type: ignore[attr-defined]

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    ProviderConfigurationError,
    TransientProviderError,
)
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.providers.litellm_provider import LiteLLMProvider


# ---------------------------------------------------------------------------
# Helpers — fake LiteLLM response objects
# ---------------------------------------------------------------------------


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 20


@dataclass
class _FakeMessage:
    content: str = "Hello from LiteLLM"


@dataclass
class _FakeChoice:
    message: _FakeMessage | None = None

    def __post_init__(self) -> None:
        if self.message is None:
            self.message = _FakeMessage()


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice] | None = None
    usage: _FakeUsage | None = None

    def __post_init__(self) -> None:
        if self.choices is None:
            self.choices = [_FakeChoice()]
        if self.usage is None:
            self.usage = _FakeUsage()


def _make_provider(
    *,
    litellm_model: str = "anthropic/claude-sonnet-4-20250514",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
) -> LiteLLMProvider:
    return LiteLLMProvider(
        litellm_model=litellm_model, provider=provider, model=model
    )


SYSTEM = "You are a helpful assistant."
MESSAGES: list[LLMMessage] = [{"role": "user", "content": "Hi"}]
MAX_TOKENS = 256
TEMPERATURE = 0.0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_returns_llm_response() -> None:
    provider = _make_provider()
    mock_acompletion = AsyncMock(return_value=_FakeResponse())

    with patch.object(litellm, "acompletion", mock_acompletion):
        result = await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello from LiteLLM"
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-4-20250514"
    assert result.input_tokens == 10
    assert result.output_tokens == 20


@pytest.mark.asyncio
async def test_complete_passes_correct_messages_to_litellm() -> None:
    provider = _make_provider()
    mock_acompletion = AsyncMock(return_value=_FakeResponse())

    with patch.object(litellm, "acompletion", mock_acompletion):
        await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

    mock_acompletion.assert_called_once_with(
        model="anthropic/claude-sonnet-4-20250514",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Hi"},
        ],
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
    )


@pytest.mark.asyncio
async def test_complete_without_system_prompt() -> None:
    """When system is empty, no system message is prepended."""
    provider = _make_provider()
    mock_acompletion = AsyncMock(return_value=_FakeResponse())

    with patch.object(litellm, "acompletion", mock_acompletion):
        await provider.complete("", MESSAGES, MAX_TOKENS, TEMPERATURE)

    call_messages = mock_acompletion.call_args.kwargs["messages"]
    assert call_messages == [{"role": "user", "content": "Hi"}]


@pytest.mark.asyncio
async def test_complete_with_multiple_messages() -> None:
    provider = _make_provider()
    multi: list[LLMMessage] = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
        {"role": "user", "content": "How are you?"},
    ]
    mock_acompletion = AsyncMock(return_value=_FakeResponse())

    with patch.object(litellm, "acompletion", mock_acompletion):
        await provider.complete(SYSTEM, multi, MAX_TOKENS, TEMPERATURE)

    call_messages = mock_acompletion.call_args.kwargs["messages"]
    assert len(call_messages) == 4  # system + 3 user/assistant


# ---------------------------------------------------------------------------
# Token extraction edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_handles_missing_usage() -> None:
    """When usage is None, tokens default to 0."""
    provider = _make_provider()
    response = _FakeResponse()
    response.usage = None
    mock_acompletion = AsyncMock(return_value=response)

    with patch.object(litellm, "acompletion", mock_acompletion):
        result = await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

    assert result.input_tokens == 0
    assert result.output_tokens == 0


@pytest.mark.asyncio
async def test_complete_handles_empty_choices() -> None:
    """When choices is empty, content is empty string."""
    provider = _make_provider()
    response = _FakeResponse()
    response.choices = []
    mock_acompletion = AsyncMock(return_value=response)

    with patch.object(litellm, "acompletion", mock_acompletion):
        result = await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

    assert result.content == ""


@pytest.mark.asyncio
async def test_complete_handles_none_message_content() -> None:
    """When message.content is None, content is empty string."""
    provider = _make_provider()
    choice = _FakeChoice()
    assert choice.message is not None
    choice.message.content = None  # type: ignore[assignment]
    response = _FakeResponse(choices=[choice])
    mock_acompletion = AsyncMock(return_value=response)

    with patch.object(litellm, "acompletion", mock_acompletion):
        result = await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

    assert result.content == ""


# ---------------------------------------------------------------------------
# Error mapping — transient (retryable)
# ---------------------------------------------------------------------------


_TRANSIENT_EXCEPTIONS: list[tuple[str, type[Exception]]] = [
    ("RateLimitError", _RateLimitError),
    ("ServiceUnavailableError", _ServiceUnavailableError),
    ("Timeout", _Timeout),
    ("APIConnectionError", _APIConnectionError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,exc_class", _TRANSIENT_EXCEPTIONS, ids=[t[0] for t in _TRANSIENT_EXCEPTIONS])
async def test_transient_errors_raise_transient_provider_error(
    name: str, exc_class: type[Exception]
) -> None:
    provider = _make_provider()
    exc_kwargs: dict[str, Any] = {
        "model": "test-model",
        "llm_provider": "test",
        "message": f"test {name}",
    }
    mock_acompletion = AsyncMock(side_effect=exc_class(**exc_kwargs))

    with patch.object(litellm, "acompletion", mock_acompletion):
        with pytest.raises(TransientProviderError):
            await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)


# ---------------------------------------------------------------------------
# Error mapping — configuration (non-retryable, auth/not-found)
# ---------------------------------------------------------------------------


_CONFIG_EXCEPTIONS: list[tuple[str, type[Exception]]] = [
    ("AuthenticationError", _AuthenticationError),
    ("NotFoundError", _NotFoundError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,exc_class", _CONFIG_EXCEPTIONS, ids=[t[0] for t in _CONFIG_EXCEPTIONS])
async def test_config_errors_raise_provider_configuration_error(
    name: str, exc_class: type[Exception]
) -> None:
    provider = _make_provider()
    exc_kwargs: dict[str, Any] = {
        "model": "test-model",
        "llm_provider": "test",
        "message": f"test {name}",
    }
    mock_acompletion = AsyncMock(side_effect=exc_class(**exc_kwargs))

    with patch.object(litellm, "acompletion", mock_acompletion):
        with pytest.raises(ProviderConfigurationError):
            await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)


# ---------------------------------------------------------------------------
# Error mapping — non-retryable (bad request, content policy, context window)
# ---------------------------------------------------------------------------


_NON_RETRYABLE_EXCEPTIONS: list[tuple[str, type[Exception]]] = [
    ("BadRequestError", _BadRequestError),
    ("ContentPolicyViolationError", _ContentPolicyViolationError),
    ("ContextWindowExceededError", _ContextWindowExceededError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,exc_class", _NON_RETRYABLE_EXCEPTIONS, ids=[t[0] for t in _NON_RETRYABLE_EXCEPTIONS])
async def test_non_retryable_errors_raise_non_retryable_provider_error(
    name: str, exc_class: type[Exception]
) -> None:
    provider = _make_provider()
    exc_kwargs: dict[str, Any] = {
        "model": "test-model",
        "llm_provider": "test",
        "message": f"test {name}",
    }
    mock_acompletion = AsyncMock(side_effect=exc_class(**exc_kwargs))

    with patch.object(litellm, "acompletion", mock_acompletion):
        with pytest.raises(NonRetryableProviderError):
            await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)


# ---------------------------------------------------------------------------
# Error mapping — generic APIError and unexpected exceptions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_api_error_raises_non_retryable() -> None:
    provider = _make_provider()
    exc = _APIError(  # type: ignore[call-arg]
        status_code=500,
        message="internal error",
        llm_provider="test",
        model="test-model",
    )
    mock_acompletion = AsyncMock(side_effect=exc)

    with patch.object(litellm, "acompletion", mock_acompletion):
        with pytest.raises(NonRetryableProviderError, match="LiteLLM API error"):
            await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)


@pytest.mark.asyncio
async def test_unexpected_exception_raises_non_retryable() -> None:
    provider = _make_provider()
    mock_acompletion = AsyncMock(side_effect=RuntimeError("something broke"))

    with patch.object(litellm, "acompletion", mock_acompletion):
        with pytest.raises(NonRetryableProviderError, match="Unexpected error"):
            await provider.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_different_provider_identities() -> None:
    """Verify provider/model strings flow through to LLMResponse."""
    for litellm_model, provider, model in [
        ("openai/gpt-4o", "openai", "gpt-4o"),
        ("xai/grok-4-1-fast-non-reasoning", "grok", "grok-4-1-fast-non-reasoning"),
        ("ollama/llama4:scout", "ollama", "llama4:scout"),
    ]:
        p = _make_provider(litellm_model=litellm_model, provider=provider, model=model)
        mock_acompletion = AsyncMock(return_value=_FakeResponse())

        with patch.object(litellm, "acompletion", mock_acompletion):
            result = await p.complete(SYSTEM, MESSAGES, MAX_TOKENS, TEMPERATURE)

        assert result.provider == provider
        assert result.model == model
        mock_acompletion.assert_called_once()
        assert mock_acompletion.call_args.kwargs["model"] == litellm_model
