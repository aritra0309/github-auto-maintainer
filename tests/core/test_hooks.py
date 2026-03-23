from __future__ import annotations

import pytest

from github_auto_maintainer.core.hooks import HookBus


@pytest.mark.asyncio
async def test_hook_bus_emits_handlers_in_registration_order() -> None:
    bus = HookBus()
    seen: list[str] = []

    def first(payload: dict[str, object]) -> None:
        seen.append(f"first:{payload['value']}")

    async def second(payload: dict[str, object]) -> None:
        seen.append(f"second:{payload['value']}")

    bus.subscribe("event", first)
    bus.subscribe("event", second)

    await bus.emit("event", {"value": "ok"})

    assert seen == ["first:ok", "second:ok"]
