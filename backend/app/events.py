"""Typed events shared by the backend and the website.

Every agent action becomes an event: stored first, then fanned out to the live feed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "run.started",
    "run.step",
    "tool.call",
    "tool.result",
    "finding",
    "proposal.created",
    "proposal.resolved",
    "action.applied",
    "message",
    "usage",
    "agent.status",
    "run.finished",
    "error",
]


class NewEvent(BaseModel):
    agent: str
    type: EventType
    run_id: str | None = None
    repo: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Event(NewEvent):
    id: int
    ts: str


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# Tokens must never reach the log, the browser or Claude.
_SECRET_RE = re.compile(
    r"(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_\-]{10,})"
)
_SECRET_KEYS = {"token", "authorization", "api_key", "apikey", "secret", "password"}
REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET_RE.sub(REDACTED, value)
    if isinstance(value, dict):
        return {
            k: REDACTED if isinstance(k, str) and k.lower() in _SECRET_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value
