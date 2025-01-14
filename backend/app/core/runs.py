"""A run is one piece of work by one agent. Everything it does is emitted as events under its run id."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ..bus import EventBus
from ..events import Event, EventType, NewEvent

log = logging.getLogger(__name__)


class RunContext:
    def __init__(self, bus: EventBus, agent: str, job: str, title: str):
        self.bus = bus
        self.agent = agent
        self.job = job
        self.title = title
        self.run_id = f"{job}-{uuid.uuid4().hex[:10]}"
        # What the run cost: requests, model calls, tokens, and model calls it managed without.
        self.counters: Counter[str] = Counter()
        self.closing_line: str | None = None
        self.failed = False

    async def emit(self, type_: EventType, payload: dict[str, Any] | None = None, repo: str | None = None) -> Event:
        return await self.bus.publish(
            NewEvent(agent=self.agent, type=type_, run_id=self.run_id, repo=repo, payload=payload or {})
        )

    async def step(self, step: str, text: str, repo: str | None = None, **extra: Any) -> Event:
        return await self.emit("run.step", {"step": step, "text": text, **extra}, repo=repo)

    async def tool(self, kind: str, **payload: Any) -> Event:
        """One call to the outside world: a GitHub request or a model call."""
        return await self.emit("tool.result", {"kind": kind, **payload})

    async def fail(self, message: str) -> None:
        """An expected failure (rate limit, bad token). The run ends as failed, without a traceback."""
        self.failed = True
        self.closing_line = message
        await self.emit("error", {"message": message})

    async def _finish(self, ok: bool, text: str) -> None:
        used = {name: n for name, n in self.counters.items() if n}
        await self.emit("usage", used)
        await self.emit("run.finished", {"ok": ok, "text": text})
        self.bus.db.bump_stats(self.agent, {**used, "runs": 1})  # the day's totals, for the metrics page


@asynccontextmanager
async def run(bus: EventBus, agent: str, job: str, title: str) -> AsyncIterator[RunContext]:
    ctx = RunContext(bus, agent, job, title)
    await ctx.emit("run.started", {"job": job, "title": title})
    try:
        yield ctx
    except asyncio.CancelledError:
        await ctx._finish(False, "Stopped.")
        raise
    except Exception as exc:
        log.exception("Run %s failed", ctx.run_id)
        await ctx.emit("error", {"message": f"Unexpected error: {exc.__class__.__name__}."})
        await ctx._finish(False, "Stopped on an unexpected error.")
        raise
    else:
        await ctx._finish(not ctx.failed, ctx.closing_line or "Done.")
