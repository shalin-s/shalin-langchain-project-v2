"""Per-thread serial queue: ensures one agent run at a time per Slack thread.

If a user sends a follow-up message while the agent is still processing the
previous one in the same thread, we queue it rather than spawning a parallel
run (which would race on the LangGraph checkpointer for that thread_id).

Workers exit after IDLE_TIMEOUT_SECONDS of no jobs so we don't hold tasks
open for every thread ever seen. submit() re-spawns a worker when needed.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

IDLE_TIMEOUT_SECONDS = 60.0

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class ThreadQueue:
    """One asyncio task per active Slack thread; jobs run serially."""

    def __init__(self, *, idle_timeout: float = IDLE_TIMEOUT_SECONDS) -> None:
        self._idle_timeout = idle_timeout
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: dict[str, asyncio.Task] = {}

    def submit(self, thread_key: str, job: Job) -> None:
        q = self._queues.get(thread_key)
        if q is None:
            q = asyncio.Queue()
            self._queues[thread_key] = q
        q.put_nowait(job)

        worker = self._workers.get(thread_key)
        if worker is None or worker.done():
            self._workers[thread_key] = asyncio.create_task(self._worker(thread_key))

    async def _worker(self, thread_key: str) -> None:
        q = self._queues[thread_key]
        while True:
            try:
                job = await asyncio.wait_for(q.get(), timeout=self._idle_timeout)
            except asyncio.TimeoutError:
                self._queues.pop(thread_key, None)
                self._workers.pop(thread_key, None)
                return
            try:
                await job()
            except Exception as e:
                logger.exception("thread %s job failed: %r", thread_key, e)
