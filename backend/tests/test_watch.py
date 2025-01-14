"""The scheduled check, and the news it leaves for the LinkedIn agent."""

import pytest

from tests.fake_github import VALID_TOKEN, FakeGitHub, FakeRepo, healthy_files
from tests.test_audit import agent_for, events, last_run, steps, usage

HEALTHY_STARS_5 = {
    "kind": "handoff", "from": "patch", "to": "pitch", "topic": "stars", "thread": "stars:octo/healthy:5",
    "text": "healthy passed 5 stars.", "data": {"name": "healthy", "stars": 5},
}  # fmt: skip


@pytest.fixture
def patch(db, bus, fake, tmp_path):
    return agent_for(db, bus, fake, tmp_path)


@pytest.fixture
def signals(bus):
    """Everything sent to live viewers without being stored."""
    seen: list[tuple[str, dict]] = []
    bus.signal = lambda name, data: seen.append((name, data))
    return seen


def handoffs(db, run_id=None):
    return [e for e in events(db, "message", run_id) if e.payload["kind"] == "handoff"]


def today(db) -> dict:
    return db.daily_stats("patch")[-1]


# -- the idle check ---------------------------------------------------------------------------


async def test_watch_waits_for_the_first_audit(patch, db, fake, signals):
    await patch.watch()
    assert fake.calls == [] and events(db) == [] and signals == []


async def test_idle_check_is_one_free_request_and_leaves_the_feed_alone(db, bus, fake, tmp_path, signals):
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()
    before, stats_before = db.last_id(), today(db)
    fake.reset()
    signals.clear()

    await patch.watch()

    assert fake.statuses() == [304]
    assert db.last_id() == before  # no run and no events: the feed stays quiet
    check = patch.last_check()
    assert (check["changed"], check["status"], check["requests"], check["free"], check["error"]) == (
        False, 304, 1, True, None,
    )  # fmt: skip
    assert signals == [("heartbeat", {"agent": "patch", **check})]

    stats = today(db)
    assert stats["idle_checks"] == 1 and stats["checks"] == stats_before["checks"] + 1
    assert stats["github_requests"] == stats_before["github_requests"] + 1
    assert stats["github_not_modified"] == stats_before.get("github_not_modified", 0) + 1
    assert stats["runs"] == stats_before["runs"]


async def test_without_a_token_an_unchanged_answer_still_counts_against_the_limit(patch, fake):
    await patch.audit()
    await patch.watch()
    check = patch.last_check()
    assert check["status"] == 304 and check["free"] is False


async def test_a_manual_audit_counts_as_a_check_too(patch, db, signals):
    await patch.audit()
    check = patch.last_check()
    assert check["changed"] is True and check["status"] == 200
    assert [name for name, _ in signals] == ["heartbeat"]
    assert today(db)["checks"] == 1 and "idle_checks" not in today(db)

    await patch.audit()  # nothing changed this time
    assert patch.last_check()["changed"] is False and today(db)["idle_checks"] == 1
    # The run already counted its request, so the check must not count it again.
    assert today(db)["github_requests"] == 8


async def test_a_failed_check_is_recorded_and_stays_out_of_the_feed(patch, db, fake, signals):
    await patch.audit()
    before = db.last_id()
    fake.rate_limited = True
    await patch.watch()

    assert db.last_id() == before
    check = patch.last_check()
    assert check["changed"] is False and check["error"].startswith("GitHub's rate limit ran out.")
    assert signals[-1] == ("heartbeat", {"agent": "patch", **check})
    assert "idle_checks" not in today(db)


# -- when something changed -------------------------------------------------------------------


async def test_a_push_starts_an_audit_and_the_whole_thing_takes_two_requests(db, bus, tmp_path):
    fake = FakeGitHub([FakeRepo(f"r{i:02}", files=healthy_files(), ci="success") for i in range(12)])
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()

    pushed = fake.repos["r05"]
    pushed.pushed_at = "2026-09-20T00:00:00Z"
    del pushed.files["LICENSE"]
    pushed.license = None
    fake.reset()
    await patch.watch()

    assert [c.path for c in fake.calls] == ["/user/repos", "/graphql"]  # the check's request is not repeated
    run_id = last_run(db)
    texts = [e.payload["text"] for e in events(db, "run.step", run_id)]
    assert texts[:2] == ["The scheduled check found a change.", "1 of 12 repositories changed. Looking inside."]
    # The request that noticed the change is shown on the run, and counted once.
    assert [t.payload["path"].split("?")[0] for t in events(db, "tool.result", run_id)] == ["/user/repos", "/graphql"]
    assert usage(db)["github_requests"] == 2 and usage(db)["repos_checked"] == 1
    assert [f.payload["check"] for f in events(db, "finding", run_id)] == ["license"]
    assert patch.last_check()["changed"] is True


async def test_a_description_change_is_audited_from_the_listing_alone(patch, db, fake):
    await patch.audit()
    fake.repos["messy"].description = "Now it says what it is."
    fake.reset()
    await patch.watch()
    assert [c.path for c in fake.calls] == ["/users/octo/repos"]
    assert steps(db, last_run(db))[0] == "sync" and usage(db)["github_requests"] == 1


# -- news for the LinkedIn agent --------------------------------------------------------------


async def test_the_first_audit_is_a_baseline_not_news(patch, db):
    await patch.audit()
    assert handoffs(db) == []


async def test_a_star_milestone_is_handed_over_without_a_run(patch, db, fake):
    await patch.audit()
    runs = len(events(db, "run.started"))
    fake.repos["healthy"].stars = 5
    await patch.watch()

    assert len(events(db, "run.started")) == runs
    [message] = handoffs(db)
    assert message.run_id is None and message.repo == "octo/healthy" and message.payload == HEALTHY_STARS_5
    assert patch.store.get("octo/healthy").snapshot.meta.stars == 5  # and the stored count is fresh

    # Losing the star and getting it back is not news a second time.
    fake.repos["healthy"].stars = 4
    await patch.watch()
    fake.repos["healthy"].stars = 6
    await patch.watch()
    assert len(handoffs(db)) == 1


async def test_a_new_repository_is_handed_over_inside_the_audit_that_found_it(patch, db, fake):
    await patch.audit()
    fake.repos["fresh"] = FakeRepo("fresh", files=healthy_files(), ci="success")
    fake.repos["rough"] = FakeRepo("rough", description=None, topics=[], license=None, files={"a.py": "x"})
    await patch.watch()

    run_id = last_run(db)
    news = {m.repo: m.payload for m in handoffs(db, run_id)}
    assert set(news) == {"octo/fresh", "octo/rough"}
    assert news["octo/fresh"]["topic"] == "new_repo"
    assert news["octo/fresh"]["text"] == "New repository: fresh. It scores 100. Worth a post."
    rough = patch.store.get("octo/rough").score
    assert news["octo/rough"]["text"] == (
        f"New repository: rough. It scores {rough}. I would fix it before anyone announces it."
    )
    assert usage(db)["handoffs"] == 2


async def test_a_live_link_and_a_fixed_up_repository_are_handed_over(patch, db, fake):
    await patch.audit()
    before = patch.store.get("octo/messy").score
    messy = fake.repos["messy"]
    messy.homepage = "https://messy.example"
    await patch.audit()
    [link] = handoffs(db, last_run(db))
    assert link.payload["topic"] == "demo_link"
    assert link.payload["text"] == "messy has a live link now: https://messy.example"

    messy.description, messy.topics, messy.license = "A tidy project.", ["python", "fastapi", "demo"], "MIT"
    messy.files, messy.ci, messy.pushed_at = healthy_files(), "success", "2026-09-28T00:00:00Z"
    await patch.audit()
    [ready] = handoffs(db, last_run(db))
    assert ready.payload["topic"] == "ready" and ready.payload["thread"] == "ready:octo/messy"
    assert ready.payload["text"] == f"messy went from {before} to 100. It is presentable now."


async def test_a_release_is_handed_over_when_it_is_newer_than_the_last_look(db, bus, fake, tmp_path):
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    fake.repos["healthy"].release = ("v0.9.0", "2024-01-01T00:00:00Z")
    await patch.audit()
    assert patch.store.get("octo/healthy").snapshot.details.latest_release == "v0.9.0"

    fake.repos["healthy"].release = ("v1.0.0", "2099-01-01T00:00:00Z")
    fake.repos["healthy"].pushed_at = "2026-09-30T00:00:00Z"  # a tag was pushed
    await patch.watch()
    [release] = handoffs(db, last_run(db))
    assert release.payload["topic"] == "release" and release.payload["text"] == "healthy shipped v1.0.0."
    assert release.payload["data"] == {"name": "healthy", "tag": "v1.0.0"}
