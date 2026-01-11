"""The job queue, the scheduler's clock, and the desk's pause switch."""

import asyncio

import pytest

from app.core.agent import AgentRegistry, Seat
from app.core.desk import Desk, is_paused
from app.core.jobs import JobQueue, Paused
from app.core.scheduler import Scheduler


class Sitter:
    """The least an agent can be, as far as the desk is concerned."""

    id = "sitter"

    def __init__(self):
        self.settled = 0

    async def settle(self) -> None:
        self.settled += 1


async def test_jobs_run_one_at_a_time_in_order():
    queue = JobQueue()
    order: list[str] = []

    async def job(name: str) -> None:
        order.append(f"{name} start")
        await asyncio.sleep(0.01)
        order.append(f"{name} end")

    assert queue.submit("a", lambda: job("a")) and queue.submit("b", lambda: job("b"))
    await queue.join()
    assert order == ["a start", "a end", "b start", "b end"]


async def test_a_job_already_waiting_is_not_queued_twice():
    queue = JobQueue()
    ran: list[int] = []

    async def job() -> None:
        ran.append(1)

    assert queue.submit("same", job) is True
    assert queue.submit("same", job) is False
    await queue.join()
    assert ran == [1]
    assert queue.submit("same", job) is True  # once it has finished, it can be asked for again
    await queue.join()


async def test_a_failing_job_does_not_stop_the_queue():
    queue = JobQueue()
    ran: list[str] = []

    async def broken() -> None:
        raise RuntimeError("boom")

    async def fine() -> None:
        ran.append("fine")

    queue.submit("broken", broken)
    queue.submit("fine", fine)
    await queue.join()
    assert ran == ["fine"] and queue.current is None


async def test_pause_stops_the_running_job_drops_the_rest_and_refuses_new_work():
    queue = JobQueue()
    started = asyncio.Event()
    log: list[str] = []

    async def long() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            log.append("cancelled")
            raise

    async def quick() -> None:
        log.append("ran")

    queue.submit("long", long)
    queue.submit("next", quick)
    await started.wait()
    assert queue.current == "long" and queue.waiting() == ["next"]

    await queue.pause()
    assert log == ["cancelled"] and queue.current is None and queue.waiting() == []
    with pytest.raises(Paused):
        queue.submit("more", quick)

    queue.resume()
    assert queue.submit("more", quick) is True
    await queue.join()
    assert log == ["cancelled", "ran"]  # the job that was waiting when paused never ran


async def test_scheduler_ticks_and_keeps_its_clock_while_paused():
    queue = JobQueue()
    ticks: list[int] = []

    async def tick() -> None:
        ticks.append(1)

    scheduler = Scheduler(queue, "watch", tick, interval=lambda: 0.03, first_delay=0.01)
    scheduler.start()
    await asyncio.sleep(0.3)
    assert len(ticks) >= 3 and scheduler.next_at is not None

    await queue.pause()
    seen = len(ticks)
    await asyncio.sleep(0.2)
    assert len(ticks) == seen  # nothing runs while paused

    queue.resume()
    await asyncio.sleep(0.2)
    assert len(ticks) > seen  # and the checks come back by themselves

    await scheduler.stop()
    await queue.join()
    assert scheduler.next_at is None


async def test_pause_is_remembered_across_a_restart(db, bus):
    registry = AgentRegistry()
    sitter = Sitter()
    registry.register(sitter)
    desk = Desk(db, bus, JobQueue(), registry)

    assert await desk.pause() is True and await desk.pause() is False
    assert is_paused(db) and Desk(db, bus, JobQueue(), registry).paused  # a fresh start reads it back
    assert await desk.resume() is True and await desk.resume() is False
    assert not is_paused(db)
    assert sitter.settled == 2  # each agent says what it is doing after every switch


async def test_pause_stops_side_work_except_the_task_that_asked_for_it(db, bus):
    desk = Desk(db, bus, JobQueue(), AgentRegistry())
    log: list[str] = []

    async def answering() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            log.append("answer stopped")
            raise

    async def asked_to_pause() -> None:
        await desk.pause()
        log.append("pause finished")  # not cancelled by its own request

    desk.spawn("chat", answering())
    await asyncio.sleep(0.01)
    assert desk.busy("chat")
    desk.spawn("command", asked_to_pause())
    await desk.join()
    assert log == ["answer stopped", "pause finished"] and not desk.busy("chat")


def test_a_reserved_seat_is_listed_until_an_agent_takes_it():
    registry = AgentRegistry()
    registry.reserve(Seat(id="sitter", name="Sitter", role="Sits"))
    assert [seat.id for seat in registry.seats()] == ["sitter"]
    registry.register(Sitter())
    assert registry.seats() == [] and registry.get("sitter") is not None
