"""What counts as news for the LinkedIn agent. Pure rules: no requests, no model."""

import pytest

from app.agents.github.handoffs import listing_news, repo_news, star_milestone
from app.agents.github.models import RepoDetails, RepoMeta, RepoReport, RepoSnapshot
from app.agents.github.store import StoredRepo

LAST_LOOK = "2026-09-10T00:00:00.000Z"


def meta(**fields) -> RepoMeta:
    return RepoMeta(full_name="octo/app", name="app", owner="octo", **fields)


def stored(score: int | None = 60, kind: str = "project", details: RepoDetails | None = None, **fields) -> StoredRepo:
    snapshot = RepoSnapshot(meta=meta(**fields), details=details or RepoDetails())
    return StoredRepo(
        full_name="octo/app", kind=kind, meta_fp="fp", pushed_at=None, snapshot=snapshot, score=score, findings=[],
        synced_at=LAST_LOOK,
    )  # fmt: skip


def report(score: int | None = 60, kind: str = "project", details: RepoDetails | None = None, **fields) -> RepoReport:
    snapshot = RepoSnapshot(meta=meta(**fields), details=details or RepoDetails())
    return RepoReport(full_name="octo/app", kind=kind, snapshot=snapshot, findings=[], score=score)


def topics(news) -> list[str]:
    return [item.topic for item in news]


def test_a_new_repository_is_news_and_says_whether_it_is_ready():
    ready = repo_news(None, report(score=91))
    assert [(n.topic, n.key, n.line, n.data) for n in ready] == [
        ("new_repo", "new_repo:octo/app", "handoff.new_repo", {"name": "app", "score": 91})
    ]
    assert repo_news(None, report(score=42))[0].line == "handoff.new_repo_rough"


@pytest.mark.parametrize(
    "fields", [{"private": True}, {"fork": True}, {"archived": True}], ids=["private", "fork", "archived"]
)
def test_only_your_own_public_projects_are_news(fields):
    assert repo_news(None, report(score=95, **fields)) == []
    assert repo_news(stored(stars=4), report(stars=30, homepage="https://x.example", **fields)) == []


def test_placeholders_and_profile_repositories_are_never_news():
    assert repo_news(None, report(score=None, kind="placeholder")) == []
    assert repo_news(None, report(score=80, kind="profile")) == []


def test_a_release_is_news_only_if_it_came_after_the_last_look():
    old = stored(details=RepoDetails(latest_release="v1.0.0"))
    newer = RepoDetails(latest_release="v1.1.0", released_at="2026-09-12T08:00:00Z")
    news = repo_news(old, report(details=newer))
    assert [(n.topic, n.key, n.data) for n in news] == [
        ("release", "release:octo/app:v1.1.0", {"name": "app", "tag": "v1.1.0"})
    ]

    # First seen now, but published long ago: adding a token must not announce old releases.
    ancient = RepoDetails(latest_release="v0.9.0", released_at="2024-01-01T00:00:00Z")
    assert repo_news(stored(), report(details=ancient)) == []
    # The same tag as last time is not news either.
    same = RepoDetails(latest_release="v1.0.0", released_at="2026-09-12T08:00:00Z")
    assert repo_news(old, report(details=same)) == []


def test_a_first_live_link_is_news_and_a_changed_one_is_not():
    news = repo_news(stored(), report(homepage="https://app.example"))
    assert [(n.topic, n.data) for n in news] == [("demo_link", {"name": "app", "url": "https://app.example"})]
    assert repo_news(stored(homepage="https://old.example"), report(homepage="https://new.example")) == []


@pytest.mark.parametrize(
    ("before", "after", "milestone"),
    [(4, 5, 5), (4, 30, 25), (5, 6, None), (26, 24, None), (99, 100, 100), (0, 0, None)],
)
def test_star_milestones(before, after, milestone):
    assert star_milestone(before, after) == milestone


def test_a_star_milestone_shows_in_the_listing_alone():
    news = listing_news(stored(stars=9), meta(stars=11))
    assert [(n.topic, n.key, n.data) for n in news] == [("stars", "stars:octo/app:10", {"name": "app", "stars": 10})]


def test_becoming_presentable_is_news_once_it_crosses_the_line():
    news = repo_news(stored(score=62), report(score=88))
    assert [(n.topic, n.key, n.data) for n in news] == [
        ("ready", "ready:octo/app", {"name": "app", "before": 62, "score": 88})
    ]
    assert repo_news(stored(score=85), report(score=95)) == []  # it already was
    assert repo_news(stored(score=85), report(score=60)) == []  # going down is not news


def test_several_things_at_once():
    old = stored(score=70, stars=24)
    new = report(score=90, stars=26, homepage="https://app.example")
    assert topics(repo_news(old, new)) == ["demo_link", "stars", "ready"]
