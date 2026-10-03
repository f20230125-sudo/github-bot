"""What every agent on the desk looks like. The LinkedIn agent will implement the same shape."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .persona import Persona

if TYPE_CHECKING:
    from .desk import Desk

Job = Callable[[], Awaitable[None]]


class Agent(Protocol):
    id: str
    persona: Persona

    def jobs(self) -> dict[str, Job]:
        """The work this agent can be asked to do, by name."""
        ...

    async def settle(self) -> None:
        """Publish what the agent is doing now that nothing is running: paused, waiting on you, or idle."""
        ...

    async def chat(self, text: str, desk: Desk) -> None:
        """Answer a message typed on the site."""
        ...


@dataclass(frozen=True)
class Seat:
    """A place at the desk for an agent that hasn't been built yet."""

    id: str
    name: str
    role: str


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._seats: dict[str, Seat] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.id] = agent

    def reserve(self, seat: Seat) -> None:
        self._seats[seat.id] = seat

    def get(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def seats(self) -> list[Seat]:
        """Seats nobody has taken yet."""
        return [seat for seat in self._seats.values() if seat.id not in self._agents]
