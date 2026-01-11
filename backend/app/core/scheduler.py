"""The clock that asks an agent to check for changes.

A check that finds nothing costs one request and makes no entry in the feed, so it can run
often. The interval is longer without a GitHub token, where requests are rationed by the hour.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress

from .jobs import JobFactory, JobQueue, Paused

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        jobs: JobQueue,
        key: str,
        tick: JobFactory,
        interval: Callable[[], float],
        first_delay: float = 5.0,
    ):
        self._jobs = jobs
        self._key = key
        self._tick = tick
        self._interval = interval
        self._first_delay = first_delay
        self._task: asyncio.Task[None] | None = None
        self.next_at: float | None = None  # when the next check is due, as a Unix time

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self.next_at = None

    async def _loop(self) -> None:
        delay = self._first_delay
        while True:
            self.next_at = time.time() + delay
            await asyncio.sleep(delay)
            try:
                self._jobs.submit(self._key, self._tick)
            except Paused:
                pass  # checks start again by themselves after you resume
            delay = self._interval()
