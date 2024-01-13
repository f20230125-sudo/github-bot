"""Proposals: changes an agent wants to make, waiting for your decision.

Nothing an agent drafts reaches the outside world from here. A proposal is applied only after
you approve it, by the agent's own fixed list of actions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from ..db import Database
from ..events import utcnow

Status = Literal["pending", "approved", "rejected", "applied", "failed", "superseded"]
# A draft with the same key as one of these is not made again.
SETTLED: tuple[Status, ...] = ("pending", "approved", "rejected", "applied")


@dataclass(frozen=True)
class Proposal:
    id: int
    agent: str
    kind: str
    repo: str | None
    title: str
    summary: str
    status: Status
    payload: dict[str, Any]
    draft_key: str
    decision: dict[str, Any] | None
    result: dict[str, Any] | None
    run_id: str | None
    created_at: str
    updated_at: str


class ProposalStore:
    def __init__(self, db: Database):
        self._db = db

    def create(
        self,
        *,
        agent: str,
        kind: str,
        repo: str | None,
        title: str,
        summary: str,
        payload: dict[str, Any],
        draft_key: str,
        run_id: str | None = None,
    ) -> Proposal:
        """Store a new pending proposal. An older pending one for the same thing is superseded."""
        now = utcnow()
        self._db.execute(
            "UPDATE proposals SET status = 'superseded', updated_at = ? "
            "WHERE agent = ? AND kind = ? AND repo IS ? AND status = 'pending'",
            (now, agent, kind, repo),
        )
        proposal_id = self._db.execute(
            "INSERT INTO proposals (agent, kind, repo, title, summary, status, payload, draft_key, run_id, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
            (agent, kind, repo, title, summary, json.dumps(payload), draft_key, run_id, now, now),
        )
        return self.get(proposal_id)  # type: ignore[return-value]

    def get(self, proposal_id: int) -> Proposal | None:
        row = self._db.query_one("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
        return _from_row(row) if row else None

    def list(self, status: Status | None = None, agent: str | None = None) -> list[Proposal]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.query(f"SELECT * FROM proposals {where} ORDER BY id DESC", tuple(params))
        return [_from_row(row) for row in rows]

    def settled(self, agent: str, draft_key: str) -> Proposal | None:
        """A proposal already made from exactly this input and not thrown away, if any."""
        marks = ", ".join("?" for _ in SETTLED)
        row = self._db.query_one(
            f"SELECT * FROM proposals WHERE agent = ? AND draft_key = ? AND status IN ({marks}) ORDER BY id DESC",
            (agent, draft_key, *SETTLED),
        )
        return _from_row(row) if row else None

    def update(
        self,
        proposal_id: int,
        *,
        status: Status | None = None,
        payload: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> Proposal:
        sets, params = ["updated_at = ?"], [utcnow()]
        for column, value in (("status", status), ("payload", payload), ("decision", decision), ("result", result)):
            if value is not None:
                sets.append(f"{column} = ?")
                params.append(value if column == "status" else json.dumps(value))
        self._db.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?", (*params, proposal_id))
        return self.get(proposal_id)  # type: ignore[return-value]


def _from_row(row) -> Proposal:
    return Proposal(
        id=row["id"],
        agent=row["agent"],
        kind=row["kind"],
        repo=row["repo"],
        title=row["title"],
        summary=row["summary"],
        status=row["status"],
        payload=json.loads(row["payload"]),
        draft_key=row["draft_key"],
        decision=json.loads(row["decision"]) if row["decision"] else None,
        result=json.loads(row["result"]) if row["result"] else None,
        run_id=row["run_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
