"""Patch's audit, end to end against a fake GitHub. The efficiency targets live here."""

import pytest

from app.agents.github.agent import PatchAgent, portfolio_summary
from app.config import Settings
from app.main import build_claude
from tests.fake_github import VALID_TOKEN, FakeGitHub, FakeRepo, healthy_files


def agent_for(db, bus, fake, tmp_path, token=None) -> PatchAgent:
    settings = Settings(
        _env_file=None, github_token=token, github_user="octo", env_path=tmp_path / ".env",
        claude_command=[], claude_cwd=tmp_path / "claude-cwd",  # an audit never needs Claude
    )  # fmt: skip
    return PatchAgent(settings, db, bus, build_claude(settings, db), transport=fake.transport())


def events(db, type_=None, run_id=None):
    out = db.events_after(0, 5000)
    if run_id:
        out = [e for e in out if e.run_id == run_id]
    return [e for e in out if type_ is None or e.type == type_]


def last_run(db) -> str:
    return events(db, "run.started")[-1].run_id


def usage(db) -> dict:
    return events(db, "usage")[-1].payload


def steps(db, run_id) -> list[str]:
    return [e.payload["step"] for e in events(db, "run.step", run_id)]


@pytest.fixture
def patch(db, bus, fake, tmp_path):
    return agent_for(db, bus, fake, tmp_path)


# -- what an audit produces -------------------------------------------------------------------


async def test_first_audit_scores_every_repository(patch, db, fake):
    await patch.audit()
    stored = patch.store.all()

    assert set(stored) == {"octo/healthy", "octo/messy", "octo/test"}
    assert stored["octo/healthy"].score == 100 and stored["octo/healthy"].findings == []
    assert stored["octo/messy"].score < 60
    assert {f.check for f in stored["octo/messy"].findings} >= {"description", "topics", "license", "readme_short", "tests_missing"}
    assert stored["octo/test"].kind == "placeholder" and stored["octo/test"].score is None

    summary = portfolio_summary(stored)
    assert summary["scored"] == 2 and summary["score"] == round((100 + stored["octo/messy"].score) / 2)


async def test_audit_emits_a_complete_run(patch, db):
    await patch.audit()
    run_id = last_run(db)
    types = [e.type for e in events(db, run_id=run_id)]

    assert types[0] == "run.started" and types[-2:] == ["usage", "run.finished"]
    assert events(db, "run.finished")[-1].payload == {"ok": True, "text": "Audit done. 7 GitHub requests, 0 model calls."}
    assert steps(db, run_id) == ["sync", "sync", "details", "checks", "repo", "repo", "repo", "summary"]

    findings = events(db, "finding", run_id)
    assert all(f.payload["status"] == "new" for f in findings)
    license_finding = next(f for f in findings if f.payload["check"] == "license")
    assert license_finding.repo == "octo/messy"
    assert license_finding.payload["text"] == "No license, so nobody can legally reuse it."

    statuses = [e.payload["status"] for e in events(db, "agent.status")]
    assert statuses == ["working", "idle"]


async def test_every_github_request_is_visible_on_the_run(patch, db, fake):
    await patch.audit()
    tools = events(db, "tool.result", last_run(db))
    assert len(tools) == len(fake.calls) == usage(db)["github_requests"]
    assert tools[0].payload["kind"] == "github" and tools[0].payload["path"].startswith("/users/octo/repos")


async def test_audit_never_calls_a_model(patch, db):
    await patch.audit()
    assert usage(db).get("claude_calls", 0) == 0
    assert usage(db)["calls_avoided"] == 3  # three repositories checked by rules


# -- efficiency targets -----------------------------------------------------------------------


async def test_idle_check_is_one_conditional_request(patch, db, fake):
    await patch.audit()
    fake.reset()
    await patch.audit()

    assert fake.statuses() == [304]
    run_id = last_run(db)
    assert steps(db, run_id) == ["sync", "sync"]
    assert events(db, "finding", run_id) == []
    assert usage(db) == {"github_requests": 1, "github_not_modified": 1, "repos_skipped": 3}


async def test_idle_check_with_a_token_says_it_was_free(db, bus, fake, tmp_path):
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()
    await patch.audit()
    texts = [e.payload["text"] for e in events(db, "run.step", last_run(db))]
    assert texts[-1] == "Nothing changed since the last look. That request used no quota."


async def test_first_full_pass_over_twelve_repositories_takes_three_requests(db, bus, tmp_path):
    fake = FakeGitHub([FakeRepo(f"r{i:02}", files=healthy_files(), ci="success") for i in range(12)])
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()

    assert [c.path for c in fake.calls] == ["/user/repos", "/graphql", "/graphql"]
    assert len(patch.store.all()) == 12 and usage(db)["github_requests"] <= 4


async def test_one_pushed_repository_takes_two_requests_and_rechecks_only_that_one(db, bus, tmp_path):
    fake = FakeGitHub([FakeRepo(f"r{i:02}", files=healthy_files(), ci="success") for i in range(12)])
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()

    pushed = fake.repos["r05"]
    pushed.pushed_at = "2026-09-20T00:00:00Z"
    del pushed.files["LICENSE"]
    pushed.license = None
    fake.reset()
    await patch.audit()

    assert [c.path for c in fake.calls] == ["/user/repos", "/graphql"]
    run_id = last_run(db)
    rechecked = [e.repo for e in events(db, "run.step", run_id) if e.payload["step"] == "repo"]
    assert rechecked == ["octo/r05"]
    assert [f.payload["check"] for f in events(db, "finding", run_id)] == ["license"]
    assert usage(db)["repos_checked"] == 1 and usage(db)["repos_skipped"] == 11


async def test_a_metadata_change_needs_no_look_inside(patch, db, fake):
    await patch.audit()
    fake.repos["messy"].description = "Now it says what it is."
    fake.reset()
    await patch.audit()

    assert [c.path for c in fake.calls] == ["/users/octo/repos"]  # the listing was enough
    run_id = last_run(db)
    resolved = [f for f in events(db, "finding", run_id) if f.payload["status"] == "resolved"]
    assert [f.payload["check"] for f in resolved] == ["description"]
    assert resolved[0].payload["text"] == "No description: fixed."
    assert "description" not in {f.check for f in patch.store.get("octo/messy").findings}


# -- change handling --------------------------------------------------------------------------


async def test_findings_already_raised_are_not_repeated(patch, db, fake):
    await patch.audit()
    fake.repos["messy"].pushed_at = "2026-09-25T00:00:00Z"
    fake.repos["messy"].files["notes.txt"] = "changed, but nothing a check cares about"
    await patch.audit()
    run_id = last_run(db)
    assert [e.repo for e in events(db, "run.step", run_id) if e.payload["step"] == "repo"] == ["octo/messy"]
    assert events(db, "finding", run_id) == []


async def test_score_history_records_changes_only(patch, db, fake):
    await patch.audit()
    await patch.audit()
    before = patch.store.score_history("octo/messy")
    assert len(before) == 1

    fake.repos["messy"].description = "Described."
    await patch.audit()
    after = patch.store.score_history("octo/messy")
    assert len(after) == 2 and after[1]["score"] == before[0]["score"] + 8


async def test_removed_repository_is_dropped(patch, db, fake):
    await patch.audit()
    del fake.repos["messy"]
    await patch.audit()
    assert "octo/messy" not in patch.store.all()
    assert "removed" in steps(db, last_run(db))


async def test_force_looks_inside_everything_again(patch, db, fake):
    await patch.audit()
    fake.reset()
    await patch.audit(force=True)
    assert len(fake.calls) == 7 and usage(db)["repos_checked"] == 3
    assert usage(db)["github_not_modified"] == 7  # nothing had changed, so every answer was a 304


# -- failures ---------------------------------------------------------------------------------


async def test_rate_limit_ends_the_run_cleanly(patch, db, fake):
    fake.rate_limited = True
    await patch.audit()
    finished = events(db, "run.finished")[-1].payload
    assert finished["ok"] is False and finished["text"].startswith("GitHub's rate limit ran out.")
    assert [e.payload["status"] for e in events(db, "agent.status")] == ["working", "idle"]


async def test_bad_token_ends_the_run_cleanly(db, bus, fake, tmp_path):
    patch = agent_for(db, bus, fake, tmp_path, token="github_pat_" + "w" * 40)
    await patch.audit()
    assert events(db, "run.finished")[-1].payload == {"ok": False, "text": "GitHub rejected the token. Check it in Setup."}
    assert "github_pat_" not in str([e.payload for e in events(db)])
