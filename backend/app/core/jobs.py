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
        # Called with a job's key once it has ended, so one agent's work can lead to another's.
        self.after: Callable[[str], None] | None = None

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
            stopped = False
            try:
                await factory()
            except asyncio.CancelledError:
                stopped = True
                raise
            except Exception:
                log.exception("Job %s failed", key)
            finally:
                self._keys.discard(key)
                self.current = None
                # Before the job counts as done: whoever waits in join() waits for what follows too.
                if self.after and not stopped:
                    self._follow(key)
                self._queue.task_done()

    def _follow(self, key: str) -> None:
        try:
            self.after(key)
        except Paused:
            pass  # paused in the meantime: nothing follows
        except Exception:
            log.exception("What follows %s could not be queued", key)
