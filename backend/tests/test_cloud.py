"""The scheduled check that runs with no desk open, and what makes it publish a new snapshot."""

import json

import httpx
import pytest

from app.agents.github.drafting import _still_needed
from app.cloud import _report, publish, run_check
from app.config import Settings
from app.core.proposals import Proposal
from app.main import create_app
from app.showcase import digest, export_snapshot
from tests.conftest import HOST, ORIGIN
from tests.fake_github import VALID_TOKEN
from tests.test_audit import agent_for


@pytest.fixture
def app(db, fake, tmp_path):
    """The desk as a scheduled job on GitHub sees it: a job token, and no Claude installed."""
    fake.job_token = True
    settings = Settings(
        _env_file=None, db_path=":memory:", github_token=VALID_TOKEN, github_user="octo", frontend_origin=ORIGIN,
        port=8010, env_path=tmp_path / ".env", claude_command=[], claude_cwd=tmp_path / "claude-cwd",
    )  # fmt: skip
    return create_app(settings, db=db, github_transport=fake.transport())


@pytest.fixture
def out(tmp_path):
    return tmp_path / "public" / "snapshot.json"


def pending(app) -> list[Proposal]:
    return app.state.proposals.list("pending")


def fix_messy(fake) -> None:
    """You add the license and the .gitignore on GitHub yourself."""
    messy = fake.repos["messy"]
    messy.files |= {"LICENSE": "MIT License", ".gitignore": "__pycache__/"}
    messy.license, messy.pushed_at = "MIT", "2026-10-05T00:00:00Z"


# -- reading with a job's token ---------------------------------------------------------------


async def test_a_job_token_falls_back_to_the_public_list_and_remembers_it(db, bus, fake, tmp_path):
    fake.job_token = True
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()

    assert [(c.path, c.status) for c in fake.calls[:2]] == [("/user/repos", 403), ("/users/octo/repos", 200)]
    assert len(patch.store.all()) == 3

    fake.reset()
    await patch.audit()
    assert [(c.path, c.status) for c in fake.calls] == [("/users/octo/repos", 304)]  # not refused a second time


async def test_a_job_token_does_not_count_as_your_own_token(app):
    await app.state.patch.audit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as client:
        assert (await client.get("/api/health")).json()["github_configured"] is False
        assert (await client.get("/api/setup")).json()["github"] == {"configured": False, "user": "octo", "mode": "public"}


async def test_your_own_token_still_lists_private_repositories(db, bus, fake, tmp_path):
    fake.repos["healthy"].private = True
    patch = agent_for(db, bus, fake, tmp_path, token=VALID_TOKEN)
    await patch.audit()
    assert [c.path for c in fake.calls][0] == "/user/repos" and "octo/healthy" in patch.store.all()


# -- one scheduled check ----------------------------------------------------------------------


async def test_the_first_run_audits_drafts_from_templates_and_publishes(app, out, fake):
    assert await run_check(app) == {"changed": True, "error": None}
    assert await publish(app, out) is True

    snapshot = json.loads(out.read_text(encoding="utf-8"))
    assert len(snapshot["digest"]) == 16 and len(snapshot["routes"]["/api/repos"]["repos"]) == 3
    assert [(p.title, p.repo) for p in pending(app)] == [("Add a license and a .gitignore", "octo/messy")]
    assert [run["job"] for run in snapshot["routes"]["/api/runs?limit=100"]["runs"]] == ["draft", "audit"]
    # No model anywhere: Claude isn't installed where the job runs.
    assert snapshot["routes"]["/api/metrics?days=1"]["today"]["claude_calls"] == 0


async def test_a_check_that_finds_nothing_is_one_free_request_and_publishes_nothing(app, out, fake):
    await run_check(app)
    await publish(app, out)
    written = out.read_text(encoding="utf-8")
    fake.reset()

    assert await run_check(app) == {"changed": False, "error": None}
    assert fake.statuses() == [304]
    assert await publish(app, out) is False and out.read_text(encoding="utf-8") == written


async def test_a_push_that_changes_nothing_you_can_see_causes_no_commit(app, out, fake):
    """The job's own commit is a push to a repository it watches. Publishing again for that would
    make every check trigger the next one."""
    await run_check(app)
    await publish(app, out)

    healthy = fake.repos["healthy"]
    healthy.pushed_at = "2026-10-05T00:00:00Z"
    healthy.files["frontend/public/showcase/snapshot.json"] = "{}"
    assert await run_check(app) == {"changed": True, "error": None}
    assert await publish(app, out) is False


async def test_fixing_it_on_github_drops_the_suggestion_and_publishes(app, out, fake, db):
    await run_check(app)
    await publish(app, out)
    [proposal] = pending(app)

    fix_messy(fake)
    assert await run_check(app) == {"changed": True, "error": None}

    assert pending(app) == []
    settled = app.state.proposals.get(proposal.id)
    assert settled.status == "superseded" and settled.result["fixed_elsewhere"] is True
    resolved = [e for e in db.events_after(0, 5000) if e.type == "proposal.resolved"][-1]
    assert resolved.payload["decision"] == "fixed" and resolved.repo == "octo/messy"
    assert resolved.payload["text"] == "Already fixed on GitHub. Dropped my proposal."

    assert await publish(app, out) is True
    snapshot = json.loads(out.read_text(encoding="utf-8"))
    assert snapshot["routes"][f"/api/proposals/{proposal.id}"]["result"]["fixed_elsewhere"] is True


async def test_a_partly_fixed_suggestion_is_redrafted_not_dropped(app, fake):
    await run_check(app)
    [before] = pending(app)
    messy = fake.repos["messy"]
    messy.files[".gitignore"] = "__pycache__/"
    messy.pushed_at = "2026-10-05T00:00:00Z"
    await run_check(app)

    [after] = pending(app)
    assert after.id != before.id and after.title == "Add a license"
    assert app.state.proposals.get(before.id).status == "superseded"


async def test_a_failed_check_reports_the_error_and_drafts_nothing(app, out, fake):
    fake.rate_limited = True
    result = await run_check(app)
    assert result["changed"] is False and result["error"].startswith("GitHub's rate limit ran out.")
    assert pending(app) == [] and not out.exists()


async def test_an_audit_that_stops_part_way_is_an_error_too(app, fake, monkeypatch):
    await run_check(app)
    fake.repos["messy"].pushed_at = "2026-10-05T00:00:00Z"

    limited = fake._route

    def route(request):
        if "/git/trees/" in request.url.path or request.url.path == "/graphql":
            return httpx.Response(403, json={"message": "limit"}, headers={"x-ratelimit-remaining": "0"})
        return limited(request)

    monkeypatch.setattr(fake, "_route", route)
    result = await run_check(app)
    assert result["changed"] is True and result["error"].startswith("GitHub's rate limit ran out.")


# -- what counts as a change worth publishing -------------------------------------------------


async def test_the_fingerprint_ignores_clocks_and_stars_but_not_substance(app, fake):
    await run_check(app)
    first = digest(await export_snapshot(app))

    fake.repos["healthy"].stars = 3  # a star is not a reason to deploy
    await run_check(app)
    assert digest(await export_snapshot(app)) == first

    fake.repos["healthy"].stars = 5  # a milestone is: it leaves a note for Pitch
    await run_check(app)
    with_note = digest(await export_snapshot(app))
    assert with_note != first

    first = with_note

    fake.repos["messy"].description = "Now it says what it is."
    await run_check(app)
    assert digest(await export_snapshot(app)) != first


def test_still_needed_for_a_metadata_sweep(db, bus, fake, tmp_path):
    def sweep(items) -> Proposal:
        return Proposal(
            id=1, agent="patch", kind="metadata_sweep", repo=None, title="Metadata sweep", summary="", status="pending",
            payload={"items": items}, draft_key="k", decision=None, result=None, run_id=None, created_at="", updated_at="",
        )  # fmt: skip

    item = {"repo": "octo/messy", "description": "A thing.", "topics": None, "enabled": True}
    assert _still_needed(sweep([item]), {}) is False  # the repository is gone


def test_report_writes_to_the_job_output(tmp_path, monkeypatch):
    target = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(target))
    _report("published", "true")
    assert target.read_text(encoding="utf-8") == "published=true\n"

    monkeypatch.delenv("GITHUB_OUTPUT")
    _report("published", "false")  # no job, nothing to write to
    assert target.read_text(encoding="utf-8") == "published=true\n"
