"""The desk as a whole: who works here, the pause switch, chat, and the notes Patch leaves Pitch."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..core.chat import history
from ..core.memory import MAX_CHARS, MIN_CHARS

router = APIRouter(prefix="/api")


def watch_view(state: Any) -> dict[str, Any]:
    settings = state.settings
    return {
        "enabled": settings.watch,
        "interval": settings.watch_interval if settings.github_token else settings.watch_interval_public,
        "next_at": state.scheduler.next_at,
        "last": state.patch.last_check(),
    }


@router.get("/agents")
async def agents(request: Request):
    state = request.app.state
    cards = [state.patch.card() | {"watch": watch_view(state)}, state.pitch.card()]
    # A seat nobody has taken yet. It is listed so the site can show it empty, not to pretend.
    cards += [asdict(seat) | {"hired": False} for seat in state.agents.seats()]
    return {"paused": state.desk.paused, "current": state.jobs.current, "agents": cards}


@router.get("/agents/{agent_id}")
async def agent_sheet(request: Request, agent_id: str):
    """One agent's own page: its voice, what it may do, and what it has learned from you."""
    state = request.app.state
    if agent_id == state.patch.id:
        return state.patch.sheet() | {"watch": watch_view(state), "paused": state.desk.paused}
    if agent_id == state.pitch.id:
        return state.pitch.sheet() | {"paused": state.desk.paused}
    raise HTTPException(404, "Nobody by that name works here yet.")


# -- lessons ----------------------------------------------------------------------------------


class LessonBody(BaseModel):
    text: str = Field(min_length=1, max_length=400)


class LessonEdit(BaseModel):
    text: str | None = Field(None, min_length=1, max_length=400)
    active: bool | None = None


UNUSABLE = f"A lesson is one line of {MIN_CHARS} to {MAX_CHARS} characters that Patch doesn't already have."


@router.post("/lessons", status_code=201)
async def add_lesson(request: Request, body: LessonBody):
    """Teach Patch something in your own words."""
    patch = request.app.state.patch
    lesson = patch.lessons.add(patch.id, body.text, "you")
    if lesson is None:
        raise HTTPException(422, UNUSABLE)
    return asdict(lesson)


@router.put("/lessons/{lesson_id}")
async def edit_lesson(request: Request, lesson_id: int, body: LessonEdit):
    lessons = request.app.state.patch.lessons
    if lessons.get(lesson_id) is None:
        raise HTTPException(404, "No such lesson.")
    lesson = lessons.update(lesson_id, text=body.text, active=body.active)
    if lesson is None:
        raise HTTPException(422, UNUSABLE)
    return asdict(lesson)


@router.delete("/lessons/{lesson_id}")
async def delete_lesson(request: Request, lesson_id: int):
    if not request.app.state.patch.lessons.delete(lesson_id):
        raise HTTPException(404, "No such lesson.")
    return {"deleted": True}


# -- the pause switch -------------------------------------------------------------------------


class PauseBody(BaseModel):
    paused: bool


@router.put("/pause")
async def set_pause(request: Request, body: PauseBody):
    """The kill switch. Pausing stops the job in progress, empties the queue and refuses new work."""
    desk = request.app.state.desk
    changed = await (desk.pause() if body.paused else desk.resume())
    return {"paused": desk.paused, "changed": changed}


class ChatBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/chat", status_code=202)
async def send_chat(request: Request, body: ChatBody):
    """Send Patch a message. The answer arrives on the event stream."""
    state = request.app.state
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Say something.")
    if state.desk.busy("chat"):
        raise HTTPException(409, "Patch is still answering your last message.")
    if not state.chat_limiter.allow():
        raise HTTPException(429, "Too many messages. Wait a minute and try again.")
    state.desk.spawn("chat", state.patch.chat(text, state.desk))
    return {"accepted": True}


@router.get("/chat")
async def chat_history(request: Request, limit: int = Query(40, ge=1, le=200)):
    state = request.app.state
    return {"messages": history(state.db, limit), "busy": state.desk.busy("chat")}


@router.get("/handoffs")
async def handoffs(request: Request, limit: int = Query(20, ge=1, le=100)):
    """What Patch has left for Pitch, newest first, each with Pitch's reading of it: whether there
    is enough for a post as things stand now, and what the post may state."""
    return {"handoffs": request.app.state.pitch.notes(limit)}
