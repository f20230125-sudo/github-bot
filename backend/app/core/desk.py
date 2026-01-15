"""The desk: one queue of work for every agent, and the switch that stops all of it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from ..bus import EventBus
from ..db import Database
from .agent import AgentRegistry
from .jobs import JobQueue

log = logging.getLogger(__name__)

PAUSED_KEY = "desk.paused"


def is_paused(db: Database) -> bool:
    return db.get_kv(PAUSED_KEY) == "1"


class Desk:
    def __init__(self, db: Database, bus: EventBus, jobs: JobQueue, agents: AgentRegistry):
        self._db = db
        self._bus = bus
        self._agents = agents
        self.jobs = jobs
        self._side: dict[str, asyncio.Task[None]] = {}
        jobs.paused = is_paused(db)  # a pause outlives a restart

    @property
    def paused(self) -> bool:
        return self.jobs.paused

    # -- work beside the queue ----------------------------------------------------------------

    def busy(self, key: str) -> bool:
        task = self._side.get(key)
        return task is not None and not task.done()

    def spawn(self, key: str, work: Coroutine[Any, Any, None]) -> None:
        """Run something beside the queue, such as answering a message while a job is running."""
        self._side[key] = asyncio.create_task(self._guarded(key, work))

    @staticmethod
    async def _guarded(key: str, work: Coroutine[Any, Any, None]) -> None:
        try:
            await work
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s failed", key)

    async def join(self) -> None:
        """Wait until everything in progress has finished. Used by tests."""
        await asyncio.gather(*(t for t in self._side.values() if not t.done()), return_exceptions=True)
        await self.jobs.join()

    # -- the kill switch ----------------------------------------------------------------------

    async def pause(self) -> bool:
        """Stop everything now and refuse new work. Returns False if the desk was already paused."""
        if self.paused:
            return False
        self._db.set_kv(PAUSED_KEY, "1")
        await self.jobs.pause()
        await self._stop_side()
        await self._settle_all()
        self._bus.signal("desk", {"paused": True})
        return True

    async def resume(self) -> bool:
        if not self.paused:
            return False
        self._db.set_kv(PAUSED_KEY, "0")
        self.jobs.resume()
        await self._settle_all()
        self._bus.signal("desk", {"paused": False})
        return True

    async def stop(self) -> None:
        """Shut down. This is not a pause: the desk works again the next time it starts."""
        await self.jobs.stop()
        await self._stop_side()

    async def _settle_all(self) -> None:
        for agent in self._agents.all():
            await agent.settle()

    async def _stop_side(self) -> None:
        # A message asking for the pause is itself side work. It must be allowed to finish.
        current = asyncio.current_task()
        tasks = [task for task in self._side.values() if not task.done() and task is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
