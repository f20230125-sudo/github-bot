import asyncio

from app.events import REDACTED, NewEvent, redact


def ev(text: str, type_: str = "run.step") -> NewEvent:
    return NewEvent(agent="patch", type=type_, run_id="r1", payload={"text": text})


async def test_publish_persists_before_fanout(bus, db):
    stored = await bus.publish(ev("one"))
    assert stored.id == 1
    assert [e.payload["text"] for e in db.events_after(0)] == ["one"]


async def test_stream_replays_backlog_then_live_without_gaps_or_repeats(bus):
    for i in range(3):
        await bus.publish(ev(f"old{i}"))

    seen: list[str] = []

    async def consume():
        async for e in bus.stream(after_id=1):
            seen.append(e.payload["text"])
            if len(seen) == 3:
                return

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    await bus.publish(ev("live"))
    await asyncio.wait_for(task, timeout=2)

    assert seen == ["old1", "old2", "live"]


async def test_stream_yields_heartbeat_when_idle(bus):
    gen = bus.stream(after_id=0, heartbeat=0.05)
    assert await asyncio.wait_for(anext(gen), timeout=1) is None
    await gen.aclose()


async def test_stream_unsubscribes_when_closed(bus):
    gen = bus.stream(after_id=0, heartbeat=0.05)
    await anext(gen)
    assert len(bus._subs) == 1
    await gen.aclose()
    assert len(bus._subs) == 0


def test_redact_masks_tokens_and_secret_keys():
    data = {
        "text": "using github_pat_" + "A" * 30 + " now",
        "nested": {"api_key": "abc", "ok": "fine"},
        "list": ["ghp_" + "b" * 30],
    }
    out = redact(data)
    assert REDACTED in out["text"] and "github_pat_" not in out["text"]
    assert out["nested"] == {"api_key": REDACTED, "ok": "fine"}
    assert out["list"] == [REDACTED]


async def test_secrets_never_reach_the_database(bus, db):
    await bus.publish(ev("token ghp_" + "c" * 30))
    assert "ghp_" not in db.latest(1)[0].payload["text"]
