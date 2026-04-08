"""In-memory idempotency store for Phase 4 write deduplication."""

from __future__ import annotations

from typing import Protocol

from github_auto_maintainer.core.actions import ActionRequest


class IdempotencyStore(Protocol):
    """Protocol for idempotency stores."""

    def is_seen(self, key: str) -> bool: ...

    def mark_seen(self, key: str) -> None: ...


class InMemoryIdempotencyStore:
    """In-memory idempotency store — state lost on restart."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_seen(self, key: str) -> bool:
        return key in self._seen

    def mark_seen(self, key: str) -> None:
        self._seen.add(key)


def build_idempotency_key(delivery_id: str, action: ActionRequest) -> str:
    """Build a deduplication key from delivery ID and action fingerprint."""
    return f"{delivery_id}::{action.fingerprint()}"
