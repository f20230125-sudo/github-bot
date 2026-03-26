"""Talking to an agent from the site.

A message climbs the same ladder as every other job. A command or a lookup is answered by rules
and costs nothing. Only an open question reaches Claude, as one call with the data it needs
attached, and its answer is checked before you see it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..db import Database

MAX_POINTS = 5
MAX_FILES = 2
POINT_MAX_CHARS = 220

COMMANDS = ("audit", "draft", "pause", "resume", "status", "approvals", "usage", "help")
# What you can type instead of a slash command. Anything else is a question.
PHRASES = {
    "audit": "audit", "run audit": "audit", "run an audit": "audit", "audit now": "audit", "start an audit": "audit",
    "draft": "draft", "draft fixes": "draft", "draft the fixes": "draft", "write the fixes": "draft",
    "pause": "pause", "stop": "pause", "stop everything": "pause",
    "resume": "resume", "continue": "resume", "unpause": "resume",
    "status": "status", "score": "status", "scores": "status",
    "approvals": "approvals", "whats waiting": "approvals", "what is waiting": "approvals",
    "usage": "usage", "limit": "usage", "limits": "usage",
    "help": "help", "commands": "help", "what can you do": "help",
}  # fmt: skip


class FileRequest(BaseModel):
    repo: str
    path: str


class ChatAnswer(BaseModel):
    """The shape Claude answers a message in."""

    say: str = ""
    points: list[str] = Field(default_factory=list)
    need_files: list[FileRequest] = Field(default_factory=list)


@dataclass(frozen=True)
class Reply:
    text: str
    points: list[str] = field(default_factory=list)
    source: str = "rules"  # "rules": answered from stored data. "claude": a model call was made.
    note: str | None = None  # why Claude wasn't used, when that needs saying


def plain(text: str) -> str:
    """Lowercase words separated by single spaces: "What's up with Foo-Bar?" -> "whats up with foo bar"."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower().replace("'", "")).split())


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """A slash command ("/audit force") or one of the exact phrases that mean the same thing."""
    text = text.strip()
    if text.startswith("/"):
        name, *args = text[1:].lower().split()[:4] or [""]
        return (name, args) if name in COMMANDS else ("help", [])
    command = PHRASES.get(plain(text))
    return (command, []) if command else None


# -- numbers must come from the data ----------------------------------------------------------

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    found: set[str] = set()
    for raw in _NUMBER_RE.findall(text):
        found.add(raw.replace(",", ""))
        found.update(part for part in re.split(r"[.,]", raw) if part)  # "2026-09-01" style pieces and "1,200"
    return found


def unverified_numbers(text: str, source: str) -> list[str]:
    """Numbers of two or more digits in `text` that appear nowhere in `source`.

    Single digits are let through: counting a handful of things is reasoning, not invention.
    """
    known = _numbers(source)
    return sorted(
        number
        for number in {raw.replace(",", "") for raw in _NUMBER_RE.findall(text)}
        if len(number.replace(".", "")) >= 2 and number not in known
    )


def in_voice(persona: Any, text: str | None, fallback: str, source: str | None = None) -> str:
    """A line an agent may say on the site. A line from Claude that breaks the agent's voice rules,
    or cites a number that isn't in `source` (the data Claude was given), is swapped for `fallback`."""
    line = " ".join((text or "").split())
    if not line or persona.lint(line) or (source is not None and unverified_numbers(line, source)):
        return fallback
    return line


# -- the conversation so far ------------------------------------------------------------------


def chat_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": payload.get("from"),
        "text": payload.get("text", ""),
        "points": payload.get("points") or [],
        "source": payload.get("source"),
        "note": payload.get("note"),
    }


def history(db: Database, limit: int = 40) -> list[dict[str, Any]]:
    """The newest `limit` chat messages, oldest first."""
    return [
        {"id": ev.id, "ts": ev.ts, "run_id": ev.run_id, "agent": ev.agent, **chat_view(ev.payload)}
        for ev in db.messages("chat", limit)
    ]
