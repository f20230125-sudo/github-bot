"""What an agent has learned from your decisions.

A rejection with a reason, or an approval where you changed the draft, can become a lesson: one
short rule the agent follows from then on. Lessons are listed on the agent's page, where you can
reword them, switch them off, delete them or add your own. The active ones are joined to the
agent's system prompt, so they shape every later draft.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from ..db import Database
from ..events import utcnow

MAX_ACTIVE = 12  # how many lessons join the system prompt: the newest ones
MAX_CHARS = 160
MIN_CHARS = 8
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿\U0001f1e6-\U0001f1ff]")

LESSON_SYSTEM = (
    "You turn one piece of feedback on a draft into at most one short, reusable writing rule. "
    "You answer with JSON only."
)


class LessonDraft(BaseModel):
    """The shape Claude answers in when asked what a decision teaches."""

    lesson: str | None = None


@dataclass(frozen=True)
class Lesson:
    id: int
    agent: str
    text: str
    source: str  # "rejection", "edit": learned from a decision. "you": written by you.
    proposal_id: int | None
    active: bool
    created_at: str


def clean_lesson(text: str | None) -> str | None:
    """One line, no bullet or quotes around it, within the length limits. Anything else is dropped."""
    cleaned = " ".join((text or "").split()).strip("-•* \"'")
    if not MIN_CHARS <= len(cleaned) <= MAX_CHARS or _EMOJI_RE.search(cleaned):
        return None
    return cleaned


def _key(text: str) -> str:
    """Two lessons that differ only in case or punctuation are the same lesson."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def lesson_prompt(owner: str, action: str, title: str, feedback: str, known: list[str]) -> str:
    """`action` is what was done to the draft ("rejected", "edited before approving")."""
    have = "\n".join(f"- {lesson}" for lesson in known) or "- none yet"
    safe = feedback.replace("</", "<\\/")  # the feedback can't close its own block
    return f"""{owner} {action} something you drafted: {title}.

Turn the feedback below into at most one rule for your future drafts.

- `lesson`: one short imperative sentence of at most 20 words, general enough to apply to other
  repositories. For example: "Keep descriptions under 100 characters."
- Give null if the feedback is only about this one case, if it is unclear, or if a rule you already
  have covers it.
- The rule must come from the feedback. Do not add advice of your own.

Rules you already have:
{have}

<feedback>
{safe}
</feedback>

Answer with JSON only, in this shape: {{"lesson": "..."}} or {{"lesson": null}}"""


class LessonStore:
    def __init__(self, db: Database):
        self._db = db

    def list(self, agent: str) -> list[Lesson]:
        """Every lesson, newest first."""
        rows = self._db.query("SELECT * FROM lessons WHERE agent = ? ORDER BY id DESC", (agent,))
        return [_from_row(row) for row in rows]

    def get(self, lesson_id: int) -> Lesson | None:
        row = self._db.query_one("SELECT * FROM lessons WHERE id = ?", (lesson_id,))
        return _from_row(row) if row else None

    def active_texts(self, agent: str) -> list[str]:
        """What joins the system prompt: the newest active lessons, oldest of them first, so adding
        one leaves the lines before it unchanged."""
        newest = [lesson for lesson in self.list(agent) if lesson.active][:MAX_ACTIVE]
        return [lesson.text for lesson in reversed(newest)]

    def add(self, agent: str, text: str | None, source: str, proposal_id: int | None = None) -> Lesson | None:
        """Store a lesson. Returns None if the text is unusable or the agent already has it."""
        cleaned = clean_lesson(text)
        if cleaned is None or self._known(agent, cleaned):
            return None
        lesson_id = self._db.execute(
            "INSERT INTO lessons (agent, text, source, proposal_id, active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
            (agent, cleaned, source, proposal_id, utcnow()),
        )
        return self.get(lesson_id)

    def update(self, lesson_id: int, text: str | None = None, active: bool | None = None) -> Lesson | None:
        """Reword a lesson or switch it on or off. Returns None if the new wording is unusable."""
        lesson = self.get(lesson_id)
        if lesson is None:
            return None
        if text is not None:
            cleaned = clean_lesson(text)
            if cleaned is None or self._known(lesson.agent, cleaned, besides=lesson_id):
                return None
            self._db.execute("UPDATE lessons SET text = ? WHERE id = ?", (cleaned, lesson_id))
        if active is not None:
            self._db.execute("UPDATE lessons SET active = ? WHERE id = ?", (1 if active else 0, lesson_id))
        return self.get(lesson_id)

    def delete(self, lesson_id: int) -> bool:
        if self.get(lesson_id) is None:
            return False
        self._db.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        return True

    def _known(self, agent: str, text: str, besides: int | None = None) -> bool:
        key = _key(text)
        return any(_key(lesson.text) == key and lesson.id != besides for lesson in self.list(agent))


def _from_row(row) -> Lesson:
    return Lesson(
        id=row["id"],
        agent=row["agent"],
        text=row["text"],
        source=row["source"],
        proposal_id=row["proposal_id"],
        active=bool(row["active"]),
        created_at=row["created_at"],
    )
