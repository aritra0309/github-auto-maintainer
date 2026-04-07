"""Async job queue abstraction with an in-memory implementation."""

from __future__ import annotations

import asyncio
from asyncio import QueueEmpty
from typing import Generic, Protocol, TypeVar

JobT = TypeVar("JobT")


class JobQueue(Protocol[JobT]):
    """Queue interface used by webhook ingress and workers."""

    async def enqueue(self, job: JobT) -> None:
        """Add a job to the queue."""

    async def dequeue(self) -> JobT:
        """Wait for and return the next available job."""

    def dequeue_nowait(self) -> JobT:
        """Return the next available job without waiting."""

    def qsize(self) -> int:
        """Return the number of currently queued jobs."""


class InMemoryJobQueue(Generic[JobT]):
    """In-process queue implementation that can be swapped later."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[JobT] = asyncio.Queue()

    async def enqueue(self, job: JobT) -> None:
        await self._queue.put(job)

    async def dequeue(self) -> JobT:
        return await self._queue.get()

    def dequeue_nowait(self) -> JobT:
        try:
            return self._queue.get_nowait()
        except QueueEmpty as exc:
            raise LookupError("Queue is empty") from exc

    def qsize(self) -> int:
        return self._queue.qsize()
