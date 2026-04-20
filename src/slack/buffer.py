"""Per-message edit coalescer for Slack chat.update calls.

Slack chat.update is rate-limited per channel and per message. Naively firing
an edit on every LangGraph event will burn rate budget and most edits will
never be seen by the user. This module queues edits per (channel, ts) pair
and flushes at most once per flush_interval, keeping only the most recent
payload.

Direct port of the BufferManager / ChannelBuffer pattern from the
personal_home_server orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

FLUSH_INTERVAL_SECONDS = 1.0

logger = logging.getLogger(__name__)

UpdateFn = Callable[[str, str, str], Awaitable[None]]


class MessageBuffer:
    """Coalesce edits per (channel, ts) and flush on a fixed cadence."""

    def __init__(
        self,
        update_fn: UpdateFn,
        *,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._update_fn = update_fn
        self._flush_interval = flush_interval
        self._pending: dict[tuple[str, str], str] = {}
        self._last_sent: dict[tuple[str, str], str] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    def queue_edit(self, channel: str, ts: str, text: str) -> None:
        self._pending[(channel, ts)] = text

    def clear(self, channel: str, ts: str) -> None:
        self._pending.pop((channel, ts), None)
        self._last_sent.pop((channel, ts), None)

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._flush_interval
                )
            except asyncio.TimeoutError:
                pass
            # Snapshot and clear so new edits during dispatch queue for next tick.
            to_send = list(self._pending.items())
            self._pending.clear()
            for (channel, ts), text in to_send:
                if self._last_sent.get((channel, ts)) == text:
                    continue
                try:
                    await self._update_fn(channel, ts, text)
                    self._last_sent[(channel, ts)] = text
                except Exception as e:
                    logger.warning("chat.update failed for %s/%s: %r", channel, ts, e)
