"""The posts Pitch has drafted, and what became of each.

A post is text on this machine and nothing more. Pitch has no way to send it anywhere: you copy
it, paste it into LinkedIn yourself, and tell Pitch that you did.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from ...db import Database
from ...events import utcnow

Status = Literal["draft", "posted", "dismissed", "replaced"]


@dataclass(frozen=True)
class Post:
    id: int
    thread: str  # the note it was written for
    repo: str | None
    status: Status
    title: str
    payload: dict[str, Any]  # angle, source, variants, hooks, picture, links, notes
    draft_key: str
    text: str | None  # your version, once you have touched it
    tone: str | None  # which version you chose
    reason: str | None  # why you passed on it
    run_id: str | None
    created_at: str
    updated_at: str


class PostStore:
    def __init__(self, db: Database):
        self._db = db

    def create(
        self, *, thread: str, repo: str | None, title: str, payload: dict[str, Any], draft_key: str, run_id: str | None
    ) -> Post:
        """Store a new draft. An older draft for the same note is replaced by it."""
        now = utcnow()
        self._db.execute(
            "UPDATE posts SET status = 'replaced', updated_at = ? WHERE thread = ? AND status = 'draft'", (now, thread)
        )
        post_id = self._db.execute(
            "INSERT INTO posts (thread, repo, status, title, payload, draft_key, run_id, created_at, updated_at) "
            "VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
            (thread, repo, title, json.dumps(payload), draft_key, run_id, now, now),
        )
        return self.get(post_id)  # type: ignore[return-value]

    def get(self, post_id: int) -> Post | None:
        row = self._db.query_one("SELECT * FROM posts WHERE id = ?", (post_id,))
        return _from_row(row) if row else None

    def list(self, status: Status | None = None) -> list[Post]:
        """Newest first. Drafts that were replaced are left out unless asked for."""
        if status:
            rows = self._db.query("SELECT * FROM posts WHERE status = ? ORDER BY id DESC", (status,))
        else:
            rows = self._db.query("SELECT * FROM posts WHERE status != 'replaced' ORDER BY id DESC")
        return [_from_row(row) for row in rows]

    def for_thread(self, thread: str) -> Post | None:
        """The newest post written for a note that has not been replaced, if any."""
        row = self._db.query_one(
            "SELECT * FROM posts WHERE thread = ? AND status != 'replaced' ORDER BY id DESC", (thread,)
        )
        return _from_row(row) if row else None

    def waiting(self, draft_key: str) -> Post | None:
        """A draft already written from exactly this input and still waiting for you, if any."""
        row = self._db.query_one(
            "SELECT * FROM posts WHERE draft_key = ? AND status = 'draft' ORDER BY id DESC", (draft_key,)
        )
        return _from_row(row) if row else None

    def update(
        self,
        post_id: int,
        *,
        status: Status | None = None,
        text: str | None = None,
        tone: str | None = None,
        reason: str | None = None,
    ) -> Post:
        sets, params = ["updated_at = ?"], [utcnow()]
        for column, value in (("status", status), ("text", text), ("tone", tone), ("reason", reason)):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value)
        self._db.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", (*params, post_id))
        return self.get(post_id)  # type: ignore[return-value]


def _from_row(row) -> Post:
    return Post(
        id=row["id"],
        thread=row["thread"],
        repo=row["repo"],
        status=row["status"],
        title=row["title"],
        payload=json.loads(row["payload"]),
        draft_key=row["draft_key"],
        text=row["text"],
        tone=row["tone"],
        reason=row["reason"],
        run_id=row["run_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
