"""Unified LLM provider adapter backed by LiteLLM."""

from __future__ import annotations

from collections.abc import Sequence

import litellm

from github_auto_maintainer.core.errors import (
    NonRetryableProviderError,
    ProviderConfigurationError,
    TransientProviderError,
)
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse

# LiteLLM doesn't export these exception classes via __all__, so mypy strict
# mode can't see them — but they exist at runtime.  We alias them here with
# targeted ignores so the rest of the module stays fully type-checked.
_AuthenticationError: type[Exception] = litellm.AuthenticationError  # type: ignore[attr-defined]
_NotFoundError: type[Exception] = litellm.NotFoundError  # type: ignore[attr-defined]
_RateLimitError: type[Exception] = litellm.RateLimitError  # type: ignore[attr-defined]
_ServiceUnavailableError: type[Exception] = litellm.ServiceUnavailableError  # type: ignore[attr-defined]
_Timeout: type[Exception] = litellm.Timeout  # type: ignore[attr-defined]
_APIConnectionError: type[Exception] = litellm.APIConnectionError  # type: ignore[attr-defined]
_BadRequestError: type[Exception] = litellm.BadRequestError  # type: ignore[attr-defined]
_ContentPolicyViolationError: type[Exception] = litellm.ContentPolicyViolationError  # type: ignore[attr-defined]
_ContextWindowExceededError: type[Exception] = litellm.ContextWindowExceededError  # type: ignore[attr-defined]
_APIError: type[Exception] = litellm.APIError  # type: ignore[attr-defined]


class LiteLLMProvider:
    """Single provider that delegates to any LiteLLM-supported backend.

    The ``litellm_model`` string controls which backend is used (e.g.
    ``"anthropic/claude-sonnet-4-20250514"``, ``"openai/gpt-4o"``,
    ``"xai/grok-4-1-fast-non-reasoning"``, ``"ollama/llama4:scout"``).

    LiteLLM reads API keys from environment variables automatically
    (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``XAI_API_KEY``, etc.).
    """

    def __init__(self, *, litellm_model: str, provider: str, model: str) -> None:
        self._litellm_model = litellm_model
        self._provider = provider
        self._model = model

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        litellm_messages: list[dict[str, str]] = []
        if system:
            litellm_messages.append({"role": "system", "content": system})
        for message in messages:
            litellm_messages.append(
                {"role": message["role"], "content": message["content"]}
            )

        try:
            response = await litellm.acompletion(
                model=self._litellm_model,
                messages=litellm_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except _AuthenticationError as exc:
            raise ProviderConfigurationError(
                f"Authentication failed for {self._litellm_model}: {exc}"
            ) from exc
        except _NotFoundError as exc:
            raise ProviderConfigurationError(
                f"Model not found: {self._litellm_model}: {exc}"
            ) from exc
        except (
            _RateLimitError,
            _ServiceUnavailableError,
            _Timeout,
            _APIConnectionError,
        ) as exc:
            raise TransientProviderError(
                f"Transient provider error for {self._litellm_model}"
            ) from exc
        except (
            _BadRequestError,
            _ContentPolicyViolationError,
            _ContextWindowExceededError,
        ) as exc:
            raise NonRetryableProviderError(
                f"Non-retryable provider error for {self._litellm_model}"
            ) from exc
        except _APIError as exc:
            raise NonRetryableProviderError(
                f"LiteLLM API error for {self._litellm_model}"
            ) from exc
        except Exception as exc:
            raise NonRetryableProviderError(
                f"Unexpected error from LiteLLM for {self._litellm_model}: {exc}"
            ) from exc

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        content = _extract_content(response)

        return LLMResponse(
            content=content,
            provider=self._provider,
            model=self._model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _extract_content(response: object) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    first = choices[0]
    message = getattr(first, "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""
