"""Provider interface contract for all LLM backends."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse


@runtime_checkable
class BaseLLMProvider(Protocol):
    """Provider interface used by the LLM router.

    Switched from ABC to Protocol so that any object with a matching
    ``complete`` signature satisfies the contract — including test fakes
    that do not explicitly subclass this type.
    """

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse: ...
