from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .db import Database
from .events import Event, NewEvent


@dataclass(frozen=True)
class Signal:
    """Something worth showing live but not worth keeping, such as "checked, nothing changed".
    It reaches whoever is watching and is not stored."""

    name: str
    data: dict[str, Any]


class EventBus:
    """Persist an event first, then fan it out to every live subscriber."""

    def __init__(self, db: Database):
        self.db = db
        self._subs: set[asyncio.Queue[Event | Signal]] = set()

    async def publish(self, ev: NewEvent) -> Event:
        stored = self.db.insert_event(ev)
        for queue in list(self._subs):
            queue.put_nowait(stored)
        return stored

    def signal(self, name: str, data: dict[str, Any]) -> None:
        for queue in list(self._subs):
            queue.put_nowait(Signal(name, data))

    async def stream(
        self, after_id: int = 0, heartbeat: float | None = None
    ) -> AsyncIterator[Event | Signal | None]:
        """Yield stored events after `after_id`, then live ones, never skipping or repeating an id.

        Subscribing before reading the backlog closes the gap between the two. When `heartbeat`
        is set, yields None after that many idle seconds so the caller can send a keepalive.
        """
        queue: asyncio.Queue[Event | Signal] = asyncio.Queue()
        self._subs.add(queue)
        try:
            last = after_id
            for ev in self.db.events_after(after_id, limit=10_000):
                last = ev.id
                yield ev
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield None
                    continue
                if isinstance(item, Signal):
                    yield item
                    continue
                if item.id <= last:
                    continue
                last = item.id
                yield item
        finally:
            self._subs.discard(queue)
