"""Metrics, run traces, Patch's own page, and the lessons endpoints."""

from datetime import date

import httpx
import pytest

from app.config import Settings
from app.core.usage_guard import UsageGuard
from app.main import create_app
from tests.claude_helpers import FakeClaude
from tests.conftest import HOST, ORIGIN, WRITE_HEADERS
from tests.test_memory import LESSON, scenario


@pytest.fixture
def claude_fake(tmp_path):
    return FakeClaude(tmp_path, scenario())


@pytest.fixture
def app(db, fake, claude_fake, tmp_path):
    settings = Settings(
        _env_file=None, db_path=":memory:", github_token=None, github_user="octo", frontend_origin=ORIGIN,
        port=8010, env_path=tmp_path / ".env", claude_command=claude_fake.command, claude_cwd=claude_fake.cwd,
    )  # fmt: skip
    return create_app(settings, db=db, github_transport=fake.transport())


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as c:
        yield c


async def post(client, app, path, **kwargs):
    response = await client.post(path, headers=WRITE_HEADERS, **kwargs)
    await app.state.desk.join()
    return response


async def worked(client, app):
    """An audit, the connection test, and a drafting run."""
    await post(client, app, "/api/jobs/audit")
    await post(client, app, "/api/claude/test")
    await post(client, app, "/api/jobs/draft")


# -- metrics ----------------------------------------------------------------------------------


async def test_metrics_before_anything_has_happened(client):
    body = (await client.get("/api/metrics")).json()
    assert len(body["days"]) == 14 and body["days"][-1]["day"] == date.today().isoformat()
    assert all(value == 0 for key, value in body["today"].items() if key != "day")
    assert body["by_job"] == [] and body["usage"]["readings"] == []
    assert body["decisions"] == {"approved": 0, "rejected": 0, "edited": 0, "pending": 0, "rate": None, "by_day": []}
    assert (await client.get("/api/metrics?days=0")).status_code == 422


async def test_metrics_count_requests_calls_and_what_rules_saved(client, app):
    await worked(client, app)
    body = (await client.get("/api/metrics?days=7")).json()
    today = body["today"]

    assert len(body["days"]) == 7 and body["totals"] == {k: v for k, v in today.items() if k != "day"}
    assert today["runs"] == 2 and today["checks"] == 1
    assert today["github_requests"] >= 7 and today["calls_avoided"] >= 3
    # One call wrote all the metadata, one wrote the README, and the connection test made one.
    assert today["claude_calls"] == 3
    jobs = {job["job"]: job for job in body["by_job"]}
    assert set(jobs) == {"sweep", "readme", "connection test"}
    assert jobs["sweep"] == {"job": "sweep", "calls": 1, "tokens_in": 900, "tokens_out": 120}
    assert today["input_tokens"] == 2700 and today["output_tokens"] == 360

    usage = body["usage"]
    assert usage["limits"] == {"session": 40.0, "weekly": 40.0} and usage["allowed"] is True
    assert [(r["window"], r["percent"]) for r in usage["readings"]] == [("session", 12.0), ("weekly", 30.0)]


async def test_metrics_show_how_your_decisions_went(client, app):
    await worked(client, app)
    proposals = {p["kind"]: p["id"] for p in (await client.get("/api/proposals")).json()["proposals"]}
    edit = {"items": [{"repo": "octo/messy", "description": "A Flask app."}]}
    assert (await post(client, app, f"/api/proposals/{proposals['metadata_sweep']}/approve", json=edit)).status_code == 202
    await post(client, app, f"/api/proposals/{proposals['pull_request']}/reject", json={"reason": ""})

    decisions = (await client.get("/api/metrics")).json()["decisions"]
    assert (decisions["approved"], decisions["rejected"], decisions["edited"], decisions["rate"]) == (1, 1, 1, 50)
    assert decisions["pending"] == 0
    assert [(d["approved"], d["rejected"]) for d in decisions["by_day"]] == [(1, 1)]


def test_usage_readings_are_logged_when_they_move(db):
    now = [1_000_000.0]
    guard = UsageGuard(db, clock=lambda: now[0])
    report = {"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.12, "resetsAt": 4102444800}

    guard.record_events([report])
    now[0] += 60
    guard.record_events([report])  # the same figure a minute later: not logged again
    now[0] += 60
    guard.record_events([report | {"utilization": 0.15}])
    now[0] += 3600
    guard.record_events([report | {"utilization": 0.15}])  # unchanged, but an hour on: one more point

    assert [(r["window"], r["percent"]) for r in guard.readings()] == [
        ("session", 12.0), ("session", 15.0), ("session", 15.0),
    ]  # fmt: skip
    now[0] += 30 * 86400
    assert guard.readings(days=7) == []


# -- runs -------------------------------------------------------------------------------------


async def test_runs_are_listed_newest_first_and_can_be_opened(client, app):
    assert (await client.get("/api/runs")).json() == {"runs": []}
    await worked(client, app)

    runs = (await client.get("/api/runs")).json()["runs"]
    assert [(r["job"], r["title"], r["ok"]) for r in runs] == [
        ("draft", "Draft fixes", True), ("audit", "Audit repositories", True),
    ]  # fmt: skip
    audit = runs[1]
    assert audit["agent"] == "patch" and audit["demo"] is False and audit["finished_at"] >= audit["started_at"]
    assert audit["text"].startswith("Audit done.") and audit["usage"]["github_requests"] == 7

    assert [r["job"] for r in (await client.get("/api/runs?job=audit")).json()["runs"]] == ["audit"]
    assert len((await client.get("/api/runs?limit=1")).json()["runs"]) == 1

    trace = (await client.get(f"/api/runs/{audit['run_id']}")).json()
    assert trace["run"] == audit
    types = [e["type"] for e in trace["events"]]
    assert types[0] == "run.started" and types[-1] == "run.finished" and types.count("tool.result") == 7
    assert (await client.get("/api/runs/audit-nope")).status_code == 404


# -- Patch's page -----------------------------------------------------------------------------


async def test_the_agent_sheet_says_who_patch_is_and_what_it_may_do(client):
    sheet = (await client.get("/api/agents/patch")).json()

    assert (sheet["id"], sheet["name"], sheet["hired"], sheet["paused"]) == ("patch", "Patch", True, False)
    assert sheet["persona"]["summary"] == "A dry senior engineer. Blunt, precise, deadpan."
    assert "Numbers over adjectives." in sheet["persona"]["rules"] and "amazing" in sheet["persona"]["banned"]
    assert (sheet["persona"]["max_chars"], sheet["persona"]["max_sentences"]) == (240, 3)
    assert list(sheet["moods"]) == ["irritated", "unimpressed", "proud", "satisfied", "focused"]

    assert sheet["writes"][0] == "Set a repository's description" and len(sheet["writes"]) == 7
    assert not any("delete" in write.lower() for write in sheet["writes"])
    assert sheet["never"][0] == "Delete a repository, a branch or a file"
    assert sheet["lessons"] == [] and sheet["lesson_limit"] == 12 and sheet["watch"]["enabled"] is True

    assert (await client.get("/api/agents/pitch")).status_code == 404  # a seat, not an agent


# -- lessons ----------------------------------------------------------------------------------


async def test_lessons_can_be_added_changed_and_removed(client):
    assert (await client.post("/api/lessons", json={"text": LESSON})).status_code == 403  # a write: needs the headers

    created = await client.post("/api/lessons", json={"text": f'  "{LESSON}" '}, headers=WRITE_HEADERS)
    assert created.status_code == 201
    lesson = created.json()
    assert (lesson["text"], lesson["source"], lesson["active"]) == (LESSON, "you", True)

    again = await client.post("/api/lessons", json={"text": LESSON.upper()}, headers=WRITE_HEADERS)
    assert again.status_code == 422 and "doesn't already have" in again.json()["detail"]
    assert (await client.post("/api/lessons", json={"text": "tiny"}, headers=WRITE_HEADERS)).status_code == 422

    path = f"/api/lessons/{lesson['id']}"
    reworded = await client.put(path, json={"text": "Descriptions: one short sentence."}, headers=WRITE_HEADERS)
    assert reworded.json()["text"] == "Descriptions: one short sentence."
    switched = await client.put(path, json={"active": False}, headers=WRITE_HEADERS)
    assert switched.json()["active"] is False and switched.json()["text"] == "Descriptions: one short sentence."

    sheet = (await client.get("/api/agents/patch")).json()
    assert [(item["text"], item["active"]) for item in sheet["lessons"]] == [("Descriptions: one short sentence.", False)]

    assert (await client.put("/api/lessons/999", json={"active": True}, headers=WRITE_HEADERS)).status_code == 404
    assert (await client.delete(path, headers=WRITE_HEADERS)).json() == {"deleted": True}
    assert (await client.delete(path, headers=WRITE_HEADERS)).status_code == 404


async def test_rejecting_with_a_reason_teaches_patch(client, app):
    await worked(client, app)
    proposals = {p["kind"]: p["id"] for p in (await client.get("/api/proposals")).json()["proposals"]}

    await post(client, app, f"/api/proposals/{proposals['metadata_sweep']}/reject", json={"reason": "Far too long."})
    [lesson] = (await client.get("/api/agents/patch")).json()["lessons"]
    assert (lesson["text"], lesson["source"], lesson["proposal_id"]) == (LESSON, "rejection", proposals["metadata_sweep"])
    assert [r["job"] for r in (await client.get("/api/runs?limit=1")).json()["runs"]] == ["learn"]

    # No reason, no lesson, and no run.
    await post(client, app, f"/api/proposals/{proposals['pull_request']}/reject")
    assert len((await client.get("/api/agents/patch")).json()["lessons"]) == 1
    assert [r["job"] for r in (await client.get("/api/runs?limit=1")).json()["runs"]] == ["learn"]


async def test_nothing_is_learned_while_paused(client, app):
    await worked(client, app)
    proposals = {p["kind"]: p["id"] for p in (await client.get("/api/proposals")).json()["proposals"]}
    await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)

    rejected = await post(client, app, f"/api/proposals/{proposals['metadata_sweep']}/reject", json={"reason": "No."})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"  # the decision still counts
    assert (await client.get("/api/agents/patch")).json()["lessons"] == []
