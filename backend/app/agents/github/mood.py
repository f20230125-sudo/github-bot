"""Patch's mood. Computed from the real state of things, never random and never from a model."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from ...core.proposals import Proposal
from .store import StoredRepo

WAITING_TOO_LONG = timedelta(hours=24)
# Each mood and what causes it, in the order they are checked. Shown on Patch's page.
MOODS = {
    "irritated": "A committed secret, or CI failing on a default branch.",
    "unimpressed": "An approval has been waiting for more than a day.",
    "proud": "The portfolio score went up in the last audit.",
    "satisfied": "The average score is 85 or more and nothing is waiting.",
    "focused": "None of the above.",
}


def compute_mood(
    repos: Iterable[StoredRepo],
    pending: list[Proposal],
    score_rose: bool = False,
    now: datetime | None = None,
) -> str:
    """One of: irritated, unimpressed, proud, satisfied, focused. The first that applies wins."""
    repos = list(repos)
    now = now or datetime.now(UTC)

    secrets = any(f.severity == "critical" for repo in repos for f in repo.findings)
    failing_ci = any(repo.snapshot.details.ci_state == "failure" for repo in repos)
    if secrets or failing_ci:
        return "irritated"

    oldest = min((_parse(p.created_at) for p in pending), default=None)
    if oldest and now - oldest > WAITING_TOO_LONG:
        return "unimpressed"

    if score_rose:
        return "proud"

    scores = [repo.score for repo in repos if repo.score is not None]
    if scores and sum(scores) / len(scores) >= 85 and not pending:
        return "satisfied"
    return "focused"


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
