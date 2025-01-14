"""News worth a post, noticed while auditing and left for the LinkedIn agent.

Each piece of news is found by comparing a repository with how it looked last time. Nothing here
calls a model or makes a request. Only public projects of your own count: a fork, a private
repository or a placeholder is never news.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import RepoMeta, RepoReport
from .store import StoredRepo

# A project at or above this score is presentable: it explains itself and can be reused.
READY_SCORE = 80
STAR_MILESTONES = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


@dataclass(frozen=True)
class Handoff:
    topic: str  # new_repo, release, demo_link, stars, ready
    repo: str
    key: str  # the same news is never handed over twice
    line: str  # which persona line says it
    data: dict[str, Any]


def _public_project(kind: str, meta: RepoMeta) -> bool:
    return kind == "project" and not (meta.private or meta.fork or meta.archived)


def _parse(timestamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else None
    except ValueError:
        return None


def star_milestone(before: int, after: int) -> int | None:
    """The highest milestone passed by going from `before` stars to `after`."""
    passed = [m for m in STAR_MILESTONES if before < m <= after]
    return passed[-1] if passed else None


def listing_news(old: StoredRepo, meta: RepoMeta) -> list[Handoff]:
    """News the repository list shows by itself: a live link, a star milestone."""
    if not _public_project(old.kind, meta):
        return []
    name, repo = meta.name, meta.full_name
    news: list[Handoff] = []
    if meta.homepage and not old.snapshot.meta.homepage:
        news.append(
            Handoff("demo_link", repo, f"demo_link:{repo}", "handoff.demo_link", {"name": name, "url": meta.homepage})
        )
    milestone = star_milestone(old.snapshot.meta.stars, meta.stars)
    if milestone:
        news.append(
            Handoff("stars", repo, f"stars:{repo}:{milestone}", "handoff.stars", {"name": name, "stars": milestone})
        )
    return news


def repo_news(old: StoredRepo | None, report: RepoReport) -> list[Handoff]:
    """News from a repository that was just re-checked. `old` is None for one never seen before."""
    meta, details = report.snapshot.meta, report.snapshot.details
    if not _public_project(report.kind, meta) or report.score is None:
        return []
    name, repo = meta.name, meta.full_name

    if old is None:
        line = "handoff.new_repo" if report.score >= READY_SCORE else "handoff.new_repo_rough"
        return [Handoff("new_repo", repo, f"new_repo:{repo}", line, {"name": name, "score": report.score})]

    news = listing_news(old, meta)

    # A release counts only if it was published after the last look, so adding a token later
    # doesn't announce releases that are years old.
    tag = details.latest_release
    published, last_look = _parse(details.released_at), _parse(old.synced_at)
    if tag and tag != old.snapshot.details.latest_release and published and last_look and published > last_look:
        news.append(Handoff("release", repo, f"release:{repo}:{tag}", "handoff.release", {"name": name, "tag": tag}))

    if old.score is not None and old.score < READY_SCORE <= report.score:
        data = {"name": name, "before": old.score, "score": report.score}
        news.append(Handoff("ready", repo, f"ready:{repo}", "handoff.ready", data))
    return news
