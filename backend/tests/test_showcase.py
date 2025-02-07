"""The snapshot behind the public, view-only site."""

import json

from app.config import Settings
from app.main import create_app
from app.showcase import PAGES, export_snapshot, summary
from tests.conftest import ORIGIN
from tests.fake_github import VALID_TOKEN


async def worked(app) -> None:
    """An audit, a lookup in chat, and a drafting run with Claude unavailable."""
    patch = app.state.patch
    await patch.audit()
    await patch.chat("what's wrong with messy?", app.state.desk)
    await patch.draft()


async def test_snapshot_holds_what_every_page_asks_for(app):
    await worked(app)
    snapshot = await export_snapshot(app)
    routes = snapshot["routes"]

    assert set(PAGES) <= set(routes)
    assert {"/api/repos/octo/healthy", "/api/repos/octo/messy", "/api/repos/octo/test"} <= set(routes)
    assert routes["/api/repos/octo/messy"]["repo"]["score"] < 60

    proposals = routes["/api/proposals?status=all"]["proposals"]
    assert proposals and all(f"/api/proposals/{p['id']}" in routes for p in proposals)
    assert routes[f"/api/proposals/{proposals[0]['id']}"]["payload"]["files"][0]["diff"]  # ready to show

    runs = routes["/api/runs?limit=100"]["runs"]
    assert [run["job"] for run in runs] == ["draft", "chat", "audit"]
    for run in runs:
        assert routes[f"/api/runs/{run['run_id']}"]["events"][0]["type"] == "run.started"

    assert [e["id"] for e in snapshot["events"]] == sorted(e["id"] for e in snapshot["events"])
    assert snapshot["events"][-1]["type"] == "agent.status" and snapshot["exported_at"].endswith("Z")
    json.dumps(snapshot)  # plain data, ready to be written to a file


async def test_snapshot_has_no_running_clock_and_nothing_in_progress(app):
    await worked(app)
    routes = (await export_snapshot(app))["routes"]
    patch, pitch = routes["/api/agents"]["agents"]

    assert "watch" not in patch and "watch" not in routes["/api/agents/patch"]
    assert routes["/api/agents"]["current"] is None and routes["/api/chat?limit=30"]["busy"] is False
    assert pitch == {"id": "pitch", "name": "Pitch", "role": "LinkedIn writer", "hired": False}
    assert [m["from"] for m in routes["/api/chat?limit=30"]["messages"]] == ["you", "patch"]


async def test_summary_says_what_becomes_public(app):
    await worked(app)
    lines = summary(await export_snapshot(app))
    assert lines[1] == "3 repositories with their scores and findings"
    assert lines[3] == "2 chat messages, word for word"
    assert lines[-1] == "no Claude plan usage figures"


async def test_the_recording_starts_at_the_latest_audit_that_looked_at_something(app, fake, db):
    patch = app.state.patch
    await patch.audit()
    fake.repos["messy"].description = "Now described."
    await patch.audit()  # looks at messy again
    await patch.audit()  # finds nothing: this one alone would make a dull recording

    audits = [e.run_id for e in db.events_after(0, 5000) if e.type == "run.started"]
    events = (await export_snapshot(app))["events"]
    started = [e["run_id"] for e in events if e["type"] == "run.started"]
    assert started == audits[1:]  # the first audit is left out, the idle one after it is kept
    assert events[0]["type"] == "agent.status" and events[0]["payload"]["status"] == "working"


async def test_an_empty_desk_still_exports(app):
    snapshot = await export_snapshot(app)
    assert snapshot["events"] == [] and snapshot["routes"]["/api/repos"]["repos"] == []


async def test_the_github_token_never_reaches_the_snapshot(db, fake, tmp_path):
    settings = Settings(
        _env_file=None, db_path=":memory:", github_token=VALID_TOKEN, github_user="octo", frontend_origin=ORIGIN,
        port=8010, env_path=tmp_path / ".env", claude_command=[], claude_cwd=tmp_path / "claude-cwd",
    )  # fmt: skip
    app = create_app(settings, db=db, github_transport=fake.transport())
    await app.state.patch.audit()

    text = json.dumps(await export_snapshot(app))
    assert VALID_TOKEN not in text and "github_pat_" not in text
    assert '"github_configured": true' in text  # it says a token exists, and nothing more
