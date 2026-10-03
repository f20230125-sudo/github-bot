"""Replay a recorded demo session through the normal event bus.

Lets the site show something before any GitHub token exists. Every replayed event
is flagged `demo: true` so the site can label it as a recording, not live work.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..bus import EventBus
from ..config import BACKEND_DIR
from ..events import NewEvent

DEMO_PATH = BACKEND_DIR / "fixtures" / "demo_session.jsonl"


def load_demo(path: Path = DEMO_PATH) -> list[tuple[float, NewEvent]]:
    steps: list[tuple[float, NewEvent]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        delay = float(raw.pop("delay", 0.0))
        raw.setdefault("payload", {})["demo"] = True
        steps.append((delay, NewEvent(**raw)))
    return steps


async def replay_demo(bus: EventBus, speed: float = 1.0, path: Path = DEMO_PATH) -> int:
    """Publish the recording. `speed` 2.0 plays twice as fast; 0 publishes everything at once."""
    steps = load_demo(path)
    for delay, event in steps:
        if speed > 0 and delay > 0:
            await asyncio.sleep(delay / speed)
        await bus.publish(event)
    return len(steps)
