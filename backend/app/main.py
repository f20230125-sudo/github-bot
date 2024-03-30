from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .agents.github.agent import PatchAgent
from .agents.linkedin.agent import PitchAgent
from .api.desk import router as desk_router
from .api.insight import router as insight_router
from .api.pitch import router as pitch_router
from .api.proposals import router as proposals_router
from .api.ratelimit import SlidingWindowLimiter
from .api.repos import router as repos_router
from .api.routes import router
from .bus import EventBus
from .config import Settings, get_settings
from .core.agent import AgentRegistry
from .core.claude import Claude
from .core.claude_cli import ClaudeCli
from .core.desk import Desk
from .core.jobs import JobQueue, Paused
from .core.scheduler import Scheduler
from .core.usage_guard import UsageGuard
from .db import Database
from .security import GuardMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FIRST_CHECK_DELAY = 10.0  # seconds after start, unless a check ran recently


def build_claude(settings: Settings, db: Database) -> Claude:
    """Claude through the Claude Code CLI signed in on this machine, behind the usage stop."""
    cli = ClaudeCli(settings.claude_command, settings.claude_cwd, timeout=settings.claude_timeout)
    guard = UsageGuard(
        db,
        session_limit=settings.stop_at_session_pct,
        weekly_limit=settings.stop_at_weekly_pct,
        fixed_allowance=settings.claude_fixed_allowance,
    )
    return Claude(cli, guard, small_model=settings.claude_model_small)


def create_app(
    settings: Settings | None = None,
    db: Database | None = None,
    github_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.watch:
            app.state.scheduler.start()
        yield
        await app.state.scheduler.stop()
        await app.state.desk.stop()
        task = getattr(app.state, "demo_task", None)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        app.state.db.close()

    app = FastAPI(title="Agent Desk", lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db or Database(settings.db_path)
    app.state.bus = EventBus(app.state.db)
    app.state.jobs = JobQueue()
    app.state.github_transport = github_transport  # tests swap in a fake GitHub
    app.state.token_limiter = SlidingWindowLimiter(5)
    app.state.test_limiter = SlidingWindowLimiter(3)
    app.state.chat_limiter = SlidingWindowLimiter(20)

    app.state.claude = build_claude(settings, app.state.db)
    patch = PatchAgent(settings, app.state.db, app.state.bus, app.state.claude, transport=github_transport)
    app.state.patch = patch
    app.state.proposals = patch.proposals
    # Pitch learns about the repositories by asking Patch, never by going to GitHub itself.
    pitch = PitchAgent(settings, app.state.db, app.state.bus, app.state.claude, source=patch)
    app.state.pitch = pitch
    app.state.agents = AgentRegistry()
    app.state.agents.register(patch)
    app.state.agents.register(pitch)
    app.state.desk = Desk(app.state.db, app.state.bus, app.state.jobs, app.state.agents)

    def then_pitch(finished: str) -> None:
        # Whatever Patch just did may have left Pitch a note, or changed what an older note is about.
        if finished.startswith(f"{patch.id}."):
            app.state.jobs.submit("pitch.read", pitch.read)

    app.state.jobs.after = then_pitch

    def watch_interval() -> float:
        return settings.watch_interval if settings.github_token else settings.watch_interval_public

    # A restart shouldn't spend a request on a check that ran a minute ago.
    since = patch.seconds_since_check()
    first = FIRST_CHECK_DELAY if since is None else max(FIRST_CHECK_DELAY, watch_interval() - since)
    app.state.scheduler = Scheduler(app.state.jobs, "patch.watch", patch.watch, watch_interval, first_delay=first)

    @app.exception_handler(Paused)
    async def refuse_while_paused(_request: Request, exc: Paused) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    allowed_hosts = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"}
    # Added first so CORS (added last) is outermost and answers preflight requests itself.
    app.add_middleware(GuardMiddleware, allowed_hosts=allowed_hosts, allowed_origin=settings.frontend_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Desk-Request", "Last-Event-ID"],
    )
    app.include_router(router)
    app.include_router(repos_router)
    app.include_router(proposals_router)
    app.include_router(desk_router)
    app.include_router(insight_router)
    app.include_router(pitch_router)
    return app


# Run with: uvicorn app.main:create_app --factory
# (No module-level `app`, so importing this module never opens the real database.)
