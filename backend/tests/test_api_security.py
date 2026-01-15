import asyncio

from app.api.routes import sse_frames
from app.core.replay import load_demo
from app.events import NewEvent
from tests.conftest import HOST, ORIGIN, WRITE_HEADERS


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["dry_run"] is True and body["github_configured"] is False


async def test_rejects_unknown_host(client):
    r = await client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


async def test_write_needs_guard_header_and_origin(client):
    assert (await client.post("/api/demo/replay?speed=0")).status_code == 403
    assert (await client.post("/api/demo/replay?speed=0", headers={"X-Desk-Request": "1"})).status_code == 403
    bad_origin = {"X-Desk-Request": "1", "Origin": "http://evil.example.com"}
    assert (await client.post("/api/demo/replay?speed=0", headers=bad_origin)).status_code == 403


async def test_reads_do_not_need_guard_header(client):
    assert (await client.get("/api/events")).status_code == 200


async def test_demo_replay_publishes_flagged_events(client, db):
    expected = len(load_demo())
    r = await client.post("/api/demo/replay?speed=0", headers=WRITE_HEADERS)
    assert r.status_code == 202
    for _ in range(100):
        if db.last_id() >= expected:
            break
        await asyncio.sleep(0.02)
    events = db.events_after(0, 5000)
    assert len(events) == expected > 10
    assert all(e.payload["demo"] is True for e in events)
    assert events[0].type == "agent.status" and events[-1].type == "agent.status"


def test_demo_recording_is_a_complete_run():
    types = [event.type for _, event in load_demo()]
    assert types.count("run.started") == types.count("run.finished") == 1
    assert "usage" in types and "finding" in types


async def test_events_endpoint_returns_latest_in_order(client, bus):
    for i in range(5):
        await bus.publish(NewEvent(agent="patch", type="run.step", payload={"i": i}))
    r = await client.get("/api/events?limit=3")
    assert [e["payload"]["i"] for e in r.json()["events"]] == [2, 3, 4]


async def test_sse_frames_format_and_resume(bus):
    for i in range(3):
        await bus.publish(NewEvent(agent="patch", type="run.step", payload={"i": i}))
    gen = sse_frames(bus, after_id=1, heartbeat=0.05)
    assert await anext(gen) == "retry: 3000\n\n"
    first = await anext(gen)
    assert first.startswith("id: 2\nevent: run.step\ndata: ")
    assert (await anext(gen)).startswith("id: 3\n")
    assert await anext(gen) == ": keepalive\n\n"
    await gen.aclose()


def test_constants_match_conftest():
    assert HOST == "127.0.0.1:8010" and ORIGIN == "http://localhost:3010"
