from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from ...db import Database
from ...events import utcnow
from .models import Finding, RepoKind, RepoReport, RepoSnapshot
from .sync import Known


@dataclass(frozen=True)
class StoredRepo:
    full_name: str
    kind: RepoKind
    meta_fp: str
    pushed_at: str | None
    snapshot: RepoSnapshot
    score: int | None
    findings: list[Finding]
    synced_at: str

    def known(self) -> Known:
        return Known(meta_fp=self.meta_fp, pushed_at=self.pushed_at, snapshot=self.snapshot)


class RepoStore:
    """The latest audit of each repository, plus how its score moved over time."""

    def __init__(self, db: Database):
        self._db = db

    def all(self) -> dict[str, StoredRepo]:
        rows = self._db.query("SELECT * FROM repos ORDER BY full_name COLLATE NOCASE")
        return {row["full_name"]: _from_row(row) for row in rows}

    def get(self, full_name: str) -> StoredRepo | None:
        row = self._db.query_one("SELECT * FROM repos WHERE full_name = ? COLLATE NOCASE", (full_name,))
        return _from_row(row) if row else None

    def save(self, report: RepoReport) -> None:
        previous = self._db.query_one("SELECT score FROM repos WHERE full_name = ?", (report.full_name,))
        now = utcnow()
        self._db.execute(
            "INSERT INTO repos (full_name, kind, snapshot, meta_fp, pushed_at, score, findings, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(full_name) DO UPDATE SET kind = excluded.kind, snapshot = excluded.snapshot, "
            "meta_fp = excluded.meta_fp, pushed_at = excluded.pushed_at, score = excluded.score, "
            "findings = excluded.findings, synced_at = excluded.synced_at",
            (
                report.full_name,
                report.kind,
                report.snapshot.model_dump_json(),
                report.snapshot.meta.fingerprint(),
                report.snapshot.meta.pushed_at,
                report.score,
                json.dumps([f.model_dump() for f in report.findings]),
                now,
            ),
        )
        if report.score is not None and (previous is None or previous["score"] != report.score):
            self._db.execute(
                "INSERT INTO repo_scores (full_name, ts, score) VALUES (?, ?, ?)",
                (report.full_name, now, report.score),
            )

    def update_snapshot(self, snapshot: RepoSnapshot) -> None:
        """Refresh stored listing data (stars, issue counts) without touching findings or history."""
        self._db.execute(
            "UPDATE repos SET snapshot = ? WHERE full_name = ?",
            (snapshot.model_dump_json(), snapshot.meta.full_name),
        )

    def delete(self, full_name: str) -> None:
        self._db.execute("DELETE FROM repos WHERE full_name = ?", (full_name,))
        self._db.execute("DELETE FROM repo_scores WHERE full_name = ?", (full_name,))

    def score_history(self, full_name: str, limit: int = 60) -> list[dict[str, str | int]]:
        rows = self._db.query(
            "SELECT ts, score FROM (SELECT id, ts, score FROM repo_scores WHERE full_name = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (full_name, limit),
        )
        return [{"ts": row["ts"], "score": row["score"]} for row in rows]


def portfolio_summary(repos: dict[str, StoredRepo]) -> dict[str, Any]:
    scores = [r.score for r in repos.values() if r.score is not None]
    counts: Counter[str] = Counter()
    for repo in repos.values():
        counts.update(f.severity for f in repo.findings)
    return {
        "repos": len(repos),
        "scored": len(scores),
        "score": round(sum(scores) / len(scores)) if scores else None,
        "findings": sum(n for severity, n in counts.items() if severity != "info"),
        "counts": dict(counts),
    }


def _from_row(row) -> StoredRepo:
    return StoredRepo(
        full_name=row["full_name"],
        kind=row["kind"],
        meta_fp=row["meta_fp"],
        pushed_at=row["pushed_at"],
        snapshot=RepoSnapshot.model_validate_json(row["snapshot"]),
        score=row["score"],
        findings=[Finding(**f) for f in json.loads(row["findings"])],
        synced_at=row["synced_at"],
    )
