from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..agents.github.client import AuthError, GitHubClient, GitHubError
from ..bus import EventBus, Signal
from ..core.envfile import update_env_file
from ..core.replay import replay_demo
from ..events import Event

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

KEEPALIVE_SECONDS = 15.0


def sse_frame(ev: Event) -> str:
    return f"id: {ev.id}\nevent: {ev.type}\ndata: {ev.model_dump_json()}\n\n"


async def sse_frames(
    bus: EventBus, after_id: int, heartbeat: float = KEEPALIVE_SECONDS
) -> AsyncIterator[str]:
    yield "retry: 3000\n\n"
    async for item in bus.stream(after_id, heartbeat=heartbeat):
        if item is None:
            yield ": keepalive\n\n"
        elif isinstance(item, Signal):
            # Not stored, so it carries no id and never moves the browser's resume point.
            yield f"event: {item.name}\ndata: {json.dumps(item.data)}\n\n"
        else:
            yield sse_frame(item)


@router.get("/health")
async def health(request: Request):
    state = request.app.state
    return {
        "status": "ok",
        "dry_run": state.patch.policy.dry_run,
        "github_configured": bool(state.settings.github_token),
        "github_user": state.settings.github_user,
        "events": state.db.last_id(),
    }


@router.get("/events")
async def list_events(
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    run_id: str | None = None,
):
    db = request.app.state.db
    if run_id:
        events = db.events_for_run(run_id)
    else:
        events = db.events_after(after, limit) if after else db.latest(limit)
    return {"events": [e.model_dump() for e in events]}


@router.get("/stream")
async def stream(
    request: Request,
    after: int | None = Query(None, ge=0),
    tail: int = Query(200, ge=0, le=500),
    last_event_id: str | None = Header(None),
):
    """Server-sent events. Resumes after `Last-Event-ID` (set by the browser on reconnect),
    else after `after`, else replays the last `tail` events so a fresh page isn't empty."""
    state = request.app.state
    if last_event_id and last_event_id.isdigit():
        start = int(last_event_id)
    elif after is not None:
        start = after
    else:
        start = max(0, state.db.last_id() - tail)
    return StreamingResponse(
        sse_frames(state.bus, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/demo/replay", status_code=202)
async def demo_replay(request: Request, speed: float = Query(1.0, ge=0, le=20)):
    state = request.app.state
    task: asyncio.Task | None = getattr(state, "demo_task", None)
    if task and not task.done():
        raise HTTPException(409, "A demo replay is already running.")

    async def run() -> None:
        try:
            await replay_demo(state.bus, speed=speed)
        except Exception:
            log.exception("Demo replay failed")

    state.demo_task = asyncio.create_task(run())
    return {"started": True, "speed": speed}


# -- jobs -------------------------------------------------------------------------------------


@router.get("/jobs")
async def jobs(request: Request):
    queue = request.app.state.jobs
    return {"current": queue.current, "waiting": queue.waiting(), "paused": queue.paused}


@router.post("/jobs/audit", status_code=202)
async def start_audit(request: Request, force: bool = False):
    state = request.app.state
    queued = state.jobs.submit("patch.audit", lambda: state.patch.audit(force=force))
    return {"queued": queued}


# -- setup ------------------------------------------------------------------------------------


class TokenBody(BaseModel):
    token: str = Field(min_length=20, max_length=400)


@router.get("/setup")
async def setup(request: Request):
    settings = request.app.state.settings
    return {
        "github": {
            "configured": bool(settings.github_token),
            "user": settings.github_user,
            # Without a token Patch can still read public repositories, in more requests.
            "mode": "token" if settings.github_token else "public",
        },
        "dry_run": request.app.state.patch.policy.dry_run,
    }


@router.put("/setup/github-token")
async def set_github_token(request: Request, body: TokenBody):
    """Check the token with GitHub, then keep it in backend/.env. It is never sent back out."""
    state = request.app.state
    if not state.token_limiter.allow():
        raise HTTPException(429, "Too many attempts. Wait a minute and try again.")

    token = body.token.strip()
    client = GitHubClient(state.db, token, transport=state.github_transport)
    try:
        user = (await client.get("/user")).data
    except AuthError:
        raise HTTPException(401, "GitHub rejected that token.") from None
    except GitHubError as exc:
        raise HTTPException(502, str(exc)) from None
    finally:
        await client.aclose()

    login = user["login"]
    state.settings.github_token = token
    state.settings.github_user = login
    update_env_file(state.settings.env_path, {"DESK_GITHUB_TOKEN": token, "DESK_GITHUB_USER": login})
    return {"user": login}


@router.delete("/setup/github-token")
async def remove_github_token(request: Request):
    state = request.app.state
    state.settings.github_token = None
    update_env_file(state.settings.env_path, {"DESK_GITHUB_TOKEN": None})
    return {"configured": False}
