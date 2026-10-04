"""Pitch: the agent that turns what Patch notices into posts for LinkedIn.

Patch leaves a note when something is worth a post. Pitch asks Patch for the facts behind it and
says whether there is enough for a post (rules only). When you ask, it writes a draft: one Claude
call, or a plain template when Claude can't be asked. You copy the draft, post it yourself, and
tell Pitch that you did. Pitch has no access to LinkedIn of any kind.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

from ...bus import EventBus
from ...config import Settings
from ...core.agent import Job
from ...core.claude import Claude
from ...core.desk import is_paused
from ...core.learning import learn_from
from ...core.memory import MAX_ACTIVE, Lesson, LessonStore
from ...core.mood import MOODS, compute_mood
from ...core.persona import Persona
from ...core.runs import run
from ...core.text import count
from ...db import Database
from ...events import Event, NewEvent
from .brief import ANGLE_LABELS, Brief, build_brief
from .learning import drafted_text, feedback_of
from .posts import Post, PostStore
from .writing import TONES, run_write

PERSONA_PATH = Path(__file__).parent / "persona.toml"
SEEN_KEY = "pitch.notes"
TONE_KEY = "pitch.tone"
MAX_NOTES = 50
LINKEDIN_CHARS = 3000  # the longest post LinkedIn accepts

DOES = (
    "Read the notes Patch leaves about your repositories",
    "Ask Patch for the facts behind a note, and for the README",
    "Say whether a note has enough for a post, and what the post may state",
    "Write a draft when you ask: one Claude call, or a plain template when Claude is off",
    "Check every draft against the facts before you see it",
    "Turn your edits, and your reasons for passing on a draft, into rules for the next one",
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

    def readme(self, full_name: str) -> str | None: ...

    def health(self) -> int | None: ...

    def owner(self) -> str: ...


class PostError(Exception):
    """A decision on a post that can't be carried out. `status` is the HTTP status it deserves."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


class PitchAgent:
    id = "pitch"

    def __init__(self, settings: Settings, db: Database, bus: EventBus, claude: Claude, source: Source):
        self.settings = settings
        self.db = db
        self.bus = bus
        self.claude = claude
        self.source = source
        self.persona = Persona.load(PERSONA_PATH)
        self.posts = PostStore(db)
        self.lessons = LessonStore(db)

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
        """Everything Pitch's own page shows about Pitch: its voice, what it does, what it never
        will, the tone you chose, and what it has learned from you."""
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
            "tone": self.tone(),
            "tones": {key: {"label": tone.label, "how": tone.how} for key, tone in TONES.items()},
            "lessons": [asdict(lesson) for lesson in self.lessons.list(self.id)],
            "lesson_limit": MAX_ACTIVE,
        }

    # -- the tone you write in ----------------------------------------------------------------

    def tone(self) -> str | None:
        """The tone you chose for your posts. None until you have picked one."""
        tone = self.db.get_kv(TONE_KEY)
        return tone if tone in TONES else None

    def set_tone(self, tone: str | None) -> str | None:
        if tone is not None and tone not in TONES:
            raise PostError(f"There is no tone called {tone}.", 422)
        self.db.set_kv(TONE_KEY, tone or "")
        return self.tone()

    # -- the notes ----------------------------------------------------------------------------

    def _handoffs(self) -> list[Event]:
        """Notes addressed to Pitch, oldest first."""
        return [ev for ev in self.db.messages("handoff", MAX_NOTES) if ev.payload.get("to") == self.id]

    def note(self, note_id: int) -> Event | None:
        return next((note for note in self._handoffs() if note.id == note_id), None)

    def look(self, note: Event) -> tuple[Brief, dict[str, Any]]:
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
        """What Patch has left, newest first, each with Pitch's reading of it as things stand now.
        Nothing here says whether a post was drafted: that stays between you and Pitch."""
        say = self.persona.line
        out: list[dict[str, Any]] = []
        for note in reversed(self._handoffs()[-limit:]):
            brief, facts = self.look(note)
            payload = note.payload
            out.append(
                {
                    "id": note.id, "ts": note.ts, "repo": note.repo, "from": payload.get("from"),
                    "to": payload.get("to"), "topic": payload.get("topic"), "text": payload.get("text"),
                    "thread": payload.get("thread") or str(note.id), "data": payload.get("data") or {},
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

    def _standing(self, with_posts: bool = True) -> tuple[str, str]:
        """Where things stand once nothing is running: drafts waiting on you, ideas, notes on hold,
        or nothing. `with_posts=False` leaves the drafts out, as if Pitch had only ever read."""
        say = self.persona.line
        notes = self._handoffs()
        if with_posts:
            drafts = len(self.posts.list("draft"))
            if drafts:
                return "waiting", say("status.drafts", drafts_text=count(drafts, "draft"))
            # An idea is a note with enough for a post that has not been written about yet.
            notes = [note for note in notes if self.posts.for_thread(self._thread(note)) is None]
        ready = sum(1 for note in notes if self.look(note)[0].ready)
        if ready:
            return "idle", say("status.ideas", ideas_text=count(ready, "idea"))
        if notes:
            return "idle", say("status.holding", notes_text=count(len(notes), "note"))
        return "idle", say("status.idle")

    async def settle(self) -> None:
        """What Pitch is doing once nothing is running: paused, waiting on you, holding ideas, or idle."""
        if is_paused(self.db):
            await self.status("paused", self.persona.line("status.paused"))
        else:
            await self.status(*self._standing())

    def public_status(self) -> dict[str, str]:
        """What Pitch would say if it had only ever read Patch's notes. This is the only status
        that may leave the desk: whether a draft exists is between you and Pitch."""
        status, text = self._standing(with_posts=False)
        return {"status": status, "text": text, "mood": self.mood()}

    @staticmethod
    def _thread(note: Event) -> str:
        return note.payload.get("thread") or str(note.id)

    # -- reading ------------------------------------------------------------------------------

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
            brief, facts = self.look(note)
            known = seen.get(self._thread(note))
            if known is None or known != brief.ready:
                fresh.append((note, brief, facts, known))
        if not fresh:
            return

        await self.status("working", say("status.reading"))
        try:
            async with run(self.bus, self.id, "read", "Read Patch's notes") as ctx:
                for note, brief, facts, known in fresh:
                    thread = self._thread(note)
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

    # -- writing ------------------------------------------------------------------------------

    def writable(self, note_id: int) -> Event:
        """The note, if a post can be written for it now. Raises PostError saying why if not."""
        note = self.note(note_id)
        if note is None:
            raise PostError("Patch has left no such note.", 404)
        brief, facts = self.look(note)
        if not brief.ready:
            raise PostError(f"Not enough for a post yet. {self._reasons(brief, facts)[0]}")
        return note

    async def write(self, note_id: int, force: bool = False) -> Post | None:
        """Write a draft for a note: one Claude call, or a template when Claude can't be asked."""
        return await run_write(self, note_id, force)

    def post_view(self, post: Post) -> dict[str, Any]:
        """A post as your own desk shows it, text and all. This never leaves this machine."""
        payload = post.payload
        return {
            "id": post.id, "thread": post.thread, "repo": post.repo, "status": post.status, "title": post.title,
            "angle_label": payload.get("angle_label"), "source": payload.get("source"),
            "variants": payload.get("variants") or [], "hooks": payload.get("hooks") or [],
            "notes": payload.get("notes") or [], "picture": payload.get("picture"),
            "text": post.text, "tone": post.tone, "reason": post.reason,
            "created_at": post.created_at, "updated_at": post.updated_at,
        }  # fmt: skip

    def _draft(self, post_id: int) -> Post:
        post = self.posts.get(post_id)
        if post is None or post.status == "replaced":
            raise PostError("No such post.", 404)
        if post.status != "draft":
            raise PostError(f"This post is already {post.status}.")
        return post

    def _yours(self, post: Post, text: str | None, tone: str | None) -> tuple[str, str]:
        """The text and the tone a decision is about, checked."""
        tones = [variant["tone"] for variant in post.payload.get("variants") or []]
        tone = tone or post.tone or (tones[0] if tones else "plain")
        if tone not in tones:
            raise PostError("That version is not part of this draft.", 422)
        text = (text if text is not None else post.text or drafted_text(post)).strip()
        if not text:
            raise PostError("The post is empty.", 422)
        if len(text) > LINKEDIN_CHARS:
            raise PostError(f"LinkedIn accepts {LINKEDIN_CHARS} characters at most. This is {len(text)}.", 422)
        return text, tone

    def save(self, post_id: int, text: str | None, tone: str | None) -> Post:
        """Keep your version of a draft, and which version you started from."""
        post = self._draft(post_id)
        text, tone = self._yours(post, text, tone)
        return self.posts.update(post.id, text=text, tone=tone)

    async def posted(self, post_id: int, text: str | None = None, tone: str | None = None) -> Post:
        """You posted it yourself. Pitch records what you posted, and the tone you chose if it had
        offered several: later drafts are written in that tone."""
        post = self._draft(post_id)
        text, tone = self._yours(post, text, tone)
        post = self.posts.update(post.id, status="posted", text=text, tone=tone)
        if self.tone() is None and len(post.payload.get("variants") or []) > 1:
            self.set_tone(tone)
        await self._said(self.persona.line("decision.posted"), post.repo)
        await self.settle()
        return post

    async def dismiss(self, post_id: int, reason: str = "") -> Post:
        post = self._draft(post_id)
        post = self.posts.update(post.id, status="dismissed", reason=" ".join(reason.split()))
        await self._said(self.persona.line("decision.dismissed"), post.repo)
        await self.settle()
        return post

    async def _said(self, text: str, repo: str | None) -> None:
        payload = {"kind": "say", "text": text}
        await self.bus.publish(NewEvent(agent=self.id, type="message", repo=repo, payload=payload))

    async def learn(self, post_id: int) -> Lesson | None:
        """Turn what you did with a draft into a rule for the next one, if it holds one."""
        post = self.posts.get(post_id)
        found = feedback_of(post)
        if post is None or found is None:
            return None  # nothing to learn from: no run and no call
        action, feedback = found
        await self.status("working", self.persona.line("status.learning"))
        try:
            return await learn_from(
                self, title=post.title, action=action, feedback=feedback, turned_down=post.status == "dismissed",
                owner=self.source.owner(), repo=post.repo,
            )  # fmt: skip
        finally:
            await self.settle()
