"""Provider interface contract for all LLM backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse


class BaseLLMProvider(ABC):
    """Abstract provider interface used by the LLM router."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Execute a completion request and return normalized response."""
