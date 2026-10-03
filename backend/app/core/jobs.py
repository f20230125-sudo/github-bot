"""Jobs run one at a time, in order. A job already waiting or running isn't queued twice.

Pausing is the kill switch: it stops the job in progress, empties the queue and refuses new work
until you resume.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

log = logging.getLogger(__name__)

JobFactory = Callable[[], Awaitable[None]]


class Paused(Exception):
    """Work was refused because the desk is paused."""


class JobQueue:
    def __init__(self, paused: bool = False) -> None:
        self._queue: asyncio.Queue[tuple[str, JobFactory]] = asyncio.Queue()
        self._keys: set[str] = set()
        self._worker: asyncio.Task[None] | None = None
        self.current: str | None = None
        self.paused = paused

    def submit(self, key: str, factory: JobFactory) -> bool:
        """Queue a job. Returns False if the same job is already waiting or running."""
        if self.paused:
            raise Paused("Paused. Resume to let the agents work.")
        if key in self._keys:
            return False
        self._keys.add(key)
        self._queue.put_nowait((key, factory))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work())
        return True

    def waiting(self) -> list[str]:
        return sorted(self._keys - ({self.current} if self.current else set()))

    async def join(self) -> None:
        """Wait until everything queued has finished. Used by tests and at shutdown."""
        await self._queue.join()

    async def pause(self) -> None:
        """Stop the job in progress, drop everything waiting, and refuse new work."""
        self.paused = True
        await self.stop()
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._keys.clear()
        self.current = None

    def resume(self) -> None:
        self.paused = False

    async def stop(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker
        self._worker = None

    async def _work(self) -> None:
        while True:
            key, factory = await self._queue.get()
            self.current = key
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Job %s failed", key)
            finally:
                self._keys.discard(key)
                self.current = None
                self._queue.task_done()
