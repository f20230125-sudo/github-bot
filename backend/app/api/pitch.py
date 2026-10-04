"""Pitch's drafts: asking for one, keeping your version, and saying what you did with it.

Nothing here reaches LinkedIn. "Posted" means you told Pitch that you posted it yourself.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..agents.linkedin.agent import LINKEDIN_CHARS, PostError
from ..agents.linkedin.learning import feedback_of
from ..agents.linkedin.posts import Post
from ..core.jobs import Paused

router = APIRouter(prefix="/api/pitch")


def _refuse(exc: PostError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


def _queue_learn(state: Any, post: Post) -> None:
    """If you changed the draft, or said why you passed on it, let Pitch turn that into a lesson."""
    if feedback_of(post) is None:
        return
    try:
        state.jobs.submit(f"pitch.learn.{post.id}", lambda: state.pitch.learn(post.id))
    except Paused:
        pass  # what you did is stored either way; while paused, nothing is learned from it


@router.get("/posts")
async def posts(request: Request):
    """Every draft and every post you marked as posted, newest first. Your desk only: this is
    never part of the public snapshot."""
    state = request.app.state
    pitch = state.pitch
    writing = [key.removeprefix("pitch.write.") for key in [state.jobs.current, *state.jobs.waiting()] if key]
    return {
        "posts": [pitch.post_view(post) for post in pitch.posts.list()],
        "tone": pitch.tone(),
        # The notes a draft is being written for right now, by id.
        "writing": [int(note) for note in writing if note.isdigit()],
        # Why Claude can't be asked, going by the last usage reading. Reading it here costs nothing
        # and waits for nothing. `claude_rechecks` says the reading will be taken again, for free,
        # when you ask for a draft: so "off" here can still turn into a written draft.
        "claude_off": await state.claude.unavailable_reason(),
        "claude_rechecks": state.claude.rechecks(),
    }


class PickBody(BaseModel):
    repo: str = Field(min_length=3, max_length=200)


@router.get("/repos")
async def pickable(request: Request):
    """Your public projects that have no note yet: what you can ask Pitch to write about yourself."""
    return {"repos": request.app.state.pitch.pickable()}


@router.post("/notes", status_code=201)
async def pick(request: Request, body: PickBody):
    """Leave Pitch a note of your own: you want a post about this repository."""
    state = request.app.state
    try:
        note = await state.pitch.pick(body.repo)
    except PostError as exc:
        raise _refuse(exc) from None
    try:
        state.jobs.submit("pitch.read", state.pitch.read)  # Pitch answers it like any other note
    except Paused:
        pass  # it is read when the desk resumes and Patch next finishes a job
    return {"id": note.id, "repo": note.repo}


@router.post("/notes/{note_id}/draft", status_code=202)
async def draft(request: Request, note_id: int, force: bool = False):
    """Ask Pitch to write a post for a note. One Claude call, or a template when Claude is off."""
    state = request.app.state
    try:
        state.pitch.writable(note_id)
    except PostError as exc:
        raise _refuse(exc) from None
    queued = state.jobs.submit(f"pitch.write.{note_id}", lambda: state.pitch.write(note_id, force=force))
    return {"queued": queued}


class YourVersion(BaseModel):
    """The text as you have it now, and which of Pitch's versions you started from."""

    text: str | None = Field(None, max_length=LINKEDIN_CHARS * 2)
    tone: str | None = Field(None, max_length=40)


@router.put("/posts/{post_id}")
async def save(request: Request, post_id: int, body: YourVersion):
    pitch = request.app.state.pitch
    try:
        return pitch.post_view(pitch.save(post_id, body.text, body.tone))
    except PostError as exc:
        raise _refuse(exc) from None


@router.post("/posts/{post_id}/posted")
async def posted(request: Request, post_id: int, body: YourVersion | None = None):
    """You posted it on LinkedIn yourself. Pitch records what you posted and learns from your edits."""
    state = request.app.state
    try:
        post = await state.pitch.posted(post_id, body.text if body else None, body.tone if body else None)
    except PostError as exc:
        raise _refuse(exc) from None
    _queue_learn(state, post)
    return state.pitch.post_view(post)


class DismissBody(BaseModel):
    reason: str = Field("", max_length=300)


@router.post("/posts/{post_id}/dismiss")
async def dismiss(request: Request, post_id: int, body: DismissBody | None = None):
    state = request.app.state
    try:
        post = await state.pitch.dismiss(post_id, body.reason if body else "")
    except PostError as exc:
        raise _refuse(exc) from None
    _queue_learn(state, post)
    return state.pitch.post_view(post)


class ToneBody(BaseModel):
    tone: str | None = Field(None, max_length=40)


@router.put("/tone")
async def set_tone(request: Request, body: ToneBody):
    """Choose the tone later drafts are written in. None means: offer every tone again."""
    try:
        return {"tone": request.app.state.pitch.set_tone(body.tone)}
    except PostError as exc:
        raise _refuse(exc) from None
