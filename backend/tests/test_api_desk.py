"""The desk endpoints: who works here, the pause switch, chat, handoffs, and live signals."""

import asyncio
import time

import httpx
import pytest

from app.api.routes import sse_frames
from app.config import Settings
from app.main import create_app
from tests.claude_helpers import USAGE_TEXT, ZERO_USAGE, FakeClaude
from tests.conftest import HOST, ORIGIN, WRITE_HEADERS

PAUSED = "Paused. Resume to let the agents work."


@pytest.fixture
def claude_fake(tmp_path):
    return FakeClaude(
        tmp_path,
        {
            "routes": [
                {"match": "/usage", "response": {"text": USAGE_TEXT, "usage": ZERO_USAGE}},
                # A question that takes Claude ten seconds, unless something stops it.
                {"match": "wrote to you on the dashboard", "response": {"hang": 10, "structured": {"say": "Late."}}},
            ],
            "responses": [{"structured": {"ok": True}}],
        },
    )


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


async def audit(client, app):
    assert (await client.post("/api/jobs/audit", headers=WRITE_HEADERS)).status_code == 202
    await app.state.desk.join()


async def say(client, app, text):
    response = await client.post("/api/chat", json={"text": text}, headers=WRITE_HEADERS)
    assert response.status_code == 202 and response.json() == {"accepted": True}
    await app.state.desk.join()


# -- who works here ---------------------------------------------------------------------------


async def test_agents_lists_patch_and_the_empty_seat(client, app):
    body = (await client.get("/api/agents")).json()
    patch, pitch = body["agents"]

    assert body["paused"] is False and body["current"] is None
    assert (patch["id"], patch["name"], patch["role"], patch["hired"]) == ("patch", "Patch", "GitHub maintainer", True)
    assert patch["status"] is None and patch["mood"] == "normal"  # nothing audited yet
    # No token, so checks are half an hour apart. The clock only runs when the server does.
    assert patch["watch"] == {"enabled": True, "interval": 1800.0, "next_at": None, "last": None}
    assert pitch == {"id": "pitch", "name": "Pitch", "role": "LinkedIn writer", "hired": False}

    await audit(client, app)
    patch = (await client.get("/api/agents")).json()["agents"][0]
    assert patch["status"]["status"] == "idle" and patch["watch"]["last"]["changed"] is True


# -- the pause switch -------------------------------------------------------------------------


async def test_pause_refuses_work_until_resumed(client, app):
    assert (await client.put("/api/pause", json={"paused": True})).status_code == 403  # a write: needs the headers

    paused = await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    assert paused.json() == {"paused": True, "changed": True}
    again = await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    assert again.json() == {"paused": True, "changed": False}

    for path in ("/api/jobs/audit", "/api/jobs/draft"):
        refused = await client.post(path, headers=WRITE_HEADERS)
        assert refused.status_code == 409 and refused.json() == {"detail": PAUSED}
    assert (await client.get("/api/jobs")).json()["paused"] is True

    body = (await client.get("/api/agents")).json()
    assert body["paused"] is True and body["agents"][0]["status"]["status"] == "paused"

    resumed = await client.put("/api/pause", json={"paused": False}, headers=WRITE_HEADERS)
    assert resumed.json() == {"paused": False, "changed": True}
    await audit(client, app)
    assert len((await client.get("/api/repos")).json()["repos"]) == 3


async def test_nothing_can_be_approved_while_paused(client, app):
    await audit(client, app)
    assert (await client.post("/api/jobs/draft", headers=WRITE_HEADERS)).status_code == 202
    await app.state.desk.join()
    proposal = (await client.get("/api/proposals")).json()["proposals"][0]

    await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    refused = await client.post(f"/api/proposals/{proposal['id']}/approve", headers=WRITE_HEADERS)
    assert refused.status_code == 409 and refused.json()["detail"].startswith("Paused.")
    assert app.state.proposals.get(proposal["id"]).status == "pending"  # nothing was recorded


async def test_pause_stops_the_job_in_progress(client, app, fake):
    await audit(client, app)
    gate = asyncio.Event()

    async def stuck() -> None:
        await gate.wait()

    app.state.jobs.submit("patch.stuck", stuck)
    app.state.jobs.submit("patch.audit", lambda: app.state.patch.audit())
    await asyncio.sleep(0.01)
    assert (await client.get("/api/jobs")).json() == {"current": "patch.stuck", "waiting": ["patch.audit"], "paused": False}

    fake.reset()
    await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    assert (await client.get("/api/jobs")).json() == {"current": None, "waiting": [], "paused": True}
    assert fake.calls == []  # the audit that was waiting never ran


# -- chat -------------------------------------------------------------------------------------


async def test_chat_is_answered_on_the_event_stream_and_kept(client, app):
    assert (await client.post("/api/chat", json={"text": "/status"})).status_code == 403
    assert (await client.post("/api/chat", json={"text": "   "}, headers=WRITE_HEADERS)).status_code == 422
    assert (await client.post("/api/chat", json={"text": "x" * 1001}, headers=WRITE_HEADERS)).status_code == 422

    await say(client, app, "/status")
    body = (await client.get("/api/chat")).json()
    assert body["busy"] is False
    assert [(m["from"], m["text"]) for m in body["messages"]] == [
        ("you", "/status"), ("patch", "Nothing has been audited yet. Say /audit."),
    ]  # fmt: skip
    assert body["messages"][1]["source"] == "rules" and body["messages"][1]["run_id"].startswith("chat-")

    await say(client, app, "/audit")  # a command typed in chat does the same as the button
    assert len((await client.get("/api/repos")).json()["repos"]) == 3
    assert len((await client.get("/api/chat?limit=2")).json()["messages"]) == 2


async def test_one_message_at_a_time_and_pause_stops_the_answer(client, app, claude_fake):
    await audit(client, app)
    await client.post("/api/claude/test", headers=WRITE_HEADERS)  # gives the usage stop its reading

    started = time.perf_counter()
    first = await client.post("/api/chat", json={"text": "Which one is the best?"}, headers=WRITE_HEADERS)
    assert first.status_code == 202
    while not any("wrote to you" in call["stdin"] for call in claude_fake.calls()):
        await asyncio.sleep(0.05)  # wait until Claude has really been called
        assert time.perf_counter() - started < 8

    busy = await client.post("/api/chat", json={"text": "/status"}, headers=WRITE_HEADERS)
    assert busy.status_code == 409 and busy.json() == {"detail": "Patch is still answering your last message."}
    assert (await client.get("/api/chat")).json()["busy"] is True

    await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    assert time.perf_counter() - started < 8  # nobody waited for the ten-second answer
    body = (await client.get("/api/chat")).json()
    assert body["busy"] is False
    assert [(m["from"], m["text"]) for m in body["messages"]] == [("you", "Which one is the best?"), ("patch", "Stopped.")]
    finished = [e for e in app.state.db.events_after(0, 5000) if e.type == "run.finished"][-1]
    assert finished.payload == {"ok": False, "text": "Stopped."}


# -- handoffs ---------------------------------------------------------------------------------


async def test_handoffs_wait_for_pitch(client, app, fake):
    assert (await client.get("/api/handoffs")).json() == {"handoffs": []}
    await audit(client, app)
    fake.repos["healthy"].stars = 10
    await app.state.patch.watch()

    [handoff] = (await client.get("/api/handoffs")).json()["handoffs"]
    assert (handoff["from"], handoff["to"], handoff["topic"], handoff["repo"]) == ("patch", "pitch", "stars", "octo/healthy")
    assert handoff["text"] == "healthy passed 10 stars." and handoff["data"] == {"name": "healthy", "stars": 10}


# -- live signals -----------------------------------------------------------------------------


async def test_signals_reach_live_viewers_and_are_not_stored(bus, db):
    frames = sse_frames(bus, 0, heartbeat=5)
    assert await anext(frames) == "retry: 3000\n\n"
    waiting = asyncio.ensure_future(anext(frames))
    await asyncio.sleep(0.05)

    bus.signal("heartbeat", {"agent": "patch", "changed": False})
    frame = await asyncio.wait_for(waiting, timeout=2)
    # No id line: a signal must never move the point a reconnecting browser resumes from.
    assert frame == 'event: heartbeat\ndata: {"agent": "patch", "changed": false}\n\n'
    assert db.last_id() == 0
    await frames.aclose()
