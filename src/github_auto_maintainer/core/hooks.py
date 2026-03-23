"""Generic event hook bus."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any


HookHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class HookBus:
    """Provider-agnostic event bus used for runtime hooks."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: HookHandler) -> None:
        """Register a handler for an event."""

        self._handlers[event_name].append(handler)

    async def emit(self, event_name: str, payload: dict[str, Any]) -> None:
        """Emit an event to all handlers in registration order."""

        for handler in self._handlers[event_name]:
            result = handler(payload)
            if result is not None:
                await result
