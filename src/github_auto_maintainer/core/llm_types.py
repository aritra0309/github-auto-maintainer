"""Shared LLM request/response types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class LLMMessage(TypedDict):
    """Normalized chat message format used across providers."""

    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized LLM response returned by all providers."""

    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    escalation_count: int = 0


class LLMHookPayload(TypedDict):
    """Hook payload emitted around LLM calls."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    prompt_hash: str
    max_tokens: int
    temperature: float
