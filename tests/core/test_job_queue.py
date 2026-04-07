from __future__ import annotations

from datetime import UTC, datetime

import pytest

from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.github.events import NormalizedEvent


@pytest.mark.asyncio
async def test_in_memory_job_queue_enqueue_and_dequeue() -> None:
    queue = InMemoryJobQueue[NormalizedEvent]()
    event = NormalizedEvent(
        event_name="pull_request.opened",
        delivery_id="delivery-1",
        github_event="pull_request",
        action="opened",
        installation_id=123,
        repository_full_name="octo/demo",
        repository_id=999,
        received_at=datetime.now(tz=UTC),
        payload={"action": "opened"},
    )

    await queue.enqueue(event)

    assert queue.qsize() == 1
    dequeued = await queue.dequeue()
    assert dequeued == event
    assert queue.qsize() == 0


def test_in_memory_job_queue_dequeue_nowait_raises_when_empty() -> None:
    queue = InMemoryJobQueue[NormalizedEvent]()

    with pytest.raises(LookupError, match="Queue is empty"):
        queue.dequeue_nowait()
