"""Pitch: the agent that turns what Patch notices into posts for LinkedIn.

So far it reads. Patch leaves a note when something is worth a post. Pitch asks Patch for the
facts behind it and says whether there is enough for a post. Rules only: no model, no request,
and no access to LinkedIn of any kind.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from ...bus import EventBus
from ...core.agent import Job
from ...core.desk import is_paused
from ...core.mood import MOODS, compute_mood
from ...core.persona import Persona
from ...core.runs import run
from ...core.text import count
from ...db import Database
from ...events import Event, NewEvent
from .brief import ANGLE_LABELS, Brief, build_brief

PERSONA_PATH = Path(__file__).parent / "persona.toml"
SEEN_KEY = "pitch.notes"
MAX_NOTES = 50

DOES = (
    "Read the notes Patch leaves about your repositories",
    "Ask Patch for the facts behind a note",
    "Say whether a note has enough for a post, and what the post may state",
)
NEVER = (
    "Sign in to LinkedIn",
    "Post, comment, react or send a message for you",
    "Read your feed, your profile or anyone else's",
    "Hold a LinkedIn password or token",
)


class Source(Protocol):
    """Who Pitch asks about the repositories. Every answer comes from stored data."""

    id: str

    def facts(self, full_name: str) -> dict[str, Any] | None: ...

    def health(self) -> int | None: ...


class PitchAgent:
    id = "pitch"

    def __init__(self, db: Database, bus: EventBus, source: Source):
        self.db = db
        self.bus = bus
        self.source = source
        self.persona = Persona.load(PERSONA_PATH)

    def jobs(self) -> dict[str, Job]:
        return {"read": self.read}

    # -- what the site shows ------------------------------------------------------------------

    def mood(self) -> str:
        return compute_mood(self.source.health())

    def card(self) -> dict[str, Any]:
        latest = self.db.latest_of(self.id, "agent.status")
        return {
            "id": self.id,
            "name": self.persona.name,
            "role": self.persona.role,
            "hired": True,
            "mood": self.mood(),
            "status": latest.payload if latest else None,
        }

    def sheet(self) -> dict[str, Any]:
        """Everything Pitch's own page shows about Pitch: its voice, what it does, and what it never will."""
        voice = self.persona.voice
        return {
            **self.card(),
            "persona": {
                "summary": voice.get("summary", ""),
                "rules": voice.get("rules", []),
                "opinions": voice.get("opinions", []),
                "quirks": voice.get("quirks", []),
                "banned": voice.get("banned", []),
                "max_chars": voice.get("max_chars", 240),
                "max_sentences": voice.get("max_sentences", 3),
            },
            "moods": MOODS,
            "does": list(DOES),
            "never": list(NEVER),
        }

    # -- the notes ----------------------------------------------------------------------------

    def _handoffs(self) -> list[Event]:
        """Notes addressed to Pitch, oldest first."""
        return [ev for ev in self.db.messages("handoff", MAX_NOTES) if ev.payload.get("to") == self.id]

    def _look(self, note: Event) -> tuple[Brief, dict[str, Any]]:
        """Pitch's reading of a note as things stand now, with the facts Patch gave for it."""
        facts = self.source.facts(note.repo) if note.repo else None
        return build_brief(note.payload.get("topic") or "", note.payload.get("data") or {}, facts), facts or {}

    def _reasons(self, brief: Brief, facts: dict[str, Any]) -> list[str]:
        """Why the note has to wait, in Pitch's words."""
        say = self.persona.line
        return [
            say(f"missing.{key}", score=facts.get("score"), needed=facts.get("presentable_from"))
            for key in brief.missing
        ]

    def notes(self, limit: int = 20) -> list[dict[str, Any]]:
        """What Patch has left, newest first, each with Pitch's reading of it as things stand now."""
        say = self.persona.line
        out: list[dict[str, Any]] = []
        for note in reversed(self._handoffs()[-limit:]):
            brief, facts = self._look(note)
            payload = note.payload
            out.append(
                {
                    "id": note.id, "ts": note.ts, "repo": note.repo, "from": payload.get("from"),
                    "to": payload.get("to"), "topic": payload.get("topic"), "text": payload.get("text"),
                    "data": payload.get("data") or {},
                    "brief": {
                        "ready": brief.ready,
                        "angle": brief.angle,
                        "angle_label": ANGLE_LABELS[brief.angle],
                        "verdict": say("verdict.ready" if brief.ready else "verdict.not_yet"),
                        "facts": [asdict(fact) for fact in brief.facts],
                        "missing": self._reasons(brief, facts),
                        "wanted": [say(f"wanted.{key}") for key in brief.wanted],
                    },
                }
            )  # fmt: skip
        return out

    # -- status -------------------------------------------------------------------------------

    async def status(self, status: str, text: str) -> None:
        payload = {"status": status, "text": text, "mood": self.mood()}
        latest = self.db.latest_of(self.id, "agent.status")
        if latest and latest.payload == payload:
            return  # already said
        await self.bus.publish(NewEvent(agent=self.id, type="agent.status", payload=payload))

    async def settle(self) -> None:
        """What Pitch is doing once nothing is running: paused, holding ideas, or idle."""
        say = self.persona.line
        if is_paused(self.db):
            await self.status("paused", say("status.paused"))
            return
        briefs = [self._look(note)[0] for note in self._handoffs()]
        ready = sum(1 for brief in briefs if brief.ready)
        if ready:
            await self.status("idle", say("status.ideas", ideas_text=count(ready, "idea")))
        elif briefs:
            await self.status("idle", say("status.holding", notes_text=count(len(briefs), "note")))
        else:
            await self.status("idle", say("status.idle"))

    # -- jobs ---------------------------------------------------------------------------------

    def _seen(self) -> dict[str, bool]:
        """Each note Pitch has answered, by its thread, with the verdict it gave."""
        return json.loads(self.db.get_kv(SEEN_KEY) or "{}")

    async def read(self) -> None:
        """Read what Patch left and answer each note: enough for a post, or not yet and why.

        A note is answered once, and again only if its verdict changes. With nothing new and
        nothing changed there is no run and nothing is added to the feed.
        """
        say = self.persona.line
        seen = self._seen()
        fresh: list[tuple[Event, Brief, dict[str, Any], bool | None]] = []
        for note in self._handoffs():
            brief, facts = self._look(note)
            known = seen.get(note.payload.get("thread") or str(note.id))
            if known is None or known != brief.ready:
                fresh.append((note, brief, facts, known))
        if not fresh:
            return

        await self.status("working", say("status.reading"))
        try:
            async with run(self.bus, self.id, "read", "Read Patch's notes") as ctx:
                for note, brief, facts, known in fresh:
                    thread = note.payload.get("thread") or str(note.id)
                    if brief.ready:
                        text = say("verdict.ready" if known is None else "verdict.now_ready")
                    else:
                        text = f"{say('verdict.not_yet')} {self._reasons(brief, facts)[0]}"
                    await ctx.emit(
                        "message",
                        {
                            "kind": "answer", "from": self.id, "to": self.source.id, "thread": thread,
                            "topic": note.payload.get("topic"), "note": note.id, "ready": brief.ready, "text": text,
                        },
                        repo=note.repo,
                    )  # fmt: skip
                    seen[thread] = brief.ready
                    self.db.set_kv(SEEN_KEY, json.dumps(seen, sort_keys=True))
                ctx.counters["notes_read"] += len(fresh)
                ready = sum(1 for _, brief, _, _ in fresh if brief.ready)
                ctx.closing_line = say("read.done", notes_text=count(len(fresh), "note"), ready=ready)
        finally:
            await self.settle()
