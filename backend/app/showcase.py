"""Export a view-only snapshot of the desk for the public demo site.

The demo site has no backend. It reads one file, and this writes that file: it asks the app, inside
this process, for exactly what the pages ask for, and saves the answers. Nothing is sent anywhere,
and neither GitHub nor Claude is called.

    python -m app.showcase

Everything in the file becomes public once you push it. The summary printed at the end says what
it holds, so read it before you commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from .config import BACKEND_DIR, get_settings
from .events import utcnow
from .main import create_app

DEFAULT_OUT = BACKEND_DIR.parent / "frontend" / "public" / "showcase" / "snapshot.json"
# What the site's pages ask for, besides one request per repository, proposal and run.
PAGES = (
    "/api/health",
    "/api/setup",
    "/api/policy",
    "/api/agents",
    "/api/agents/patch",
    "/api/repos",
    "/api/proposals?status=all",
    "/api/chat?limit=30",
    "/api/handoffs",
    "/api/runs?limit=100",
    "/api/metrics?days=1",
    "/api/metrics?days=7",
    "/api/metrics?days=14",
    "/api/metrics?days=30",
)
MAX_EVENTS = 400


def _without_clock(card: dict[str, Any]) -> dict[str, Any]:
    """Drop "checked 2 minutes ago, next check in 3": a snapshot has no clock running."""
    return {key: value for key, value in card.items() if key != "watch"}


async def export_snapshot(app: FastAPI) -> dict[str, Any]:
    """Everything the view-only site shows, as {exported_at, events, routes: {path: answer}}."""
    port = app.state.settings.port
    transport = httpx.ASGITransport(app=app)
    routes: dict[str, Any] = {}

    async with httpx.AsyncClient(transport=transport, base_url=f"http://127.0.0.1:{port}") as client:

        async def get(path: str) -> Any:
            response = await client.get(path)
            response.raise_for_status()
            return response.json()

        for path in PAGES:
            routes[path] = await get(path)
        for repo in routes["/api/repos"]["repos"]:
            path = f"/api/repos/{repo['owner']}/{repo['name']}"
            routes[path] = await get(path)
        for proposal in routes["/api/proposals?status=all"]["proposals"]:
            path = f"/api/proposals/{proposal['id']}"
            routes[path] = await get(path)
        for run in routes["/api/runs?limit=100"]["runs"]:
            path = f"/api/runs/{run['run_id']}"
            routes[path] = await get(path)
        events = (await get(f"/api/events?limit={MAX_EVENTS}"))["events"]

    desk = routes["/api/agents"]
    desk["agents"] = [_without_clock(card) for card in desk["agents"]]
    desk["current"] = None
    routes["/api/agents/patch"] = _without_clock(routes["/api/agents/patch"])
    routes["/api/chat?limit=30"]["busy"] = False
    return {"exported_at": utcnow(), "events": events, "routes": routes}


def summary(snapshot: dict[str, Any]) -> list[str]:
    """What the snapshot holds, in plain words, so you can decide whether to publish it."""
    routes = snapshot["routes"]
    chat = routes["/api/chat?limit=30"]["messages"]
    usage = routes["/api/metrics?days=30"]["usage"]["windows"]
    reported = [name for name, window in usage.items() if window["percent"] is not None]
    return [
        f"{len(snapshot['events'])} events from {len(routes['/api/runs?limit=100']['runs'])} runs",
        f"{len(routes['/api/repos']['repos'])} repositories with their scores and findings",
        f"{len(routes['/api/proposals?status=all']['proposals'])} proposals, with the full text of every file in them",
        f"{len(chat)} chat messages, word for word",
        f"{len(routes['/api/agents/patch']['lessons'])} lessons",
        "your Claude plan usage percentages" if reported else "no Claude plan usage figures",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a view-only snapshot for the public demo site.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the snapshot")
    args = parser.parse_args()

    app = create_app(get_settings())
    snapshot = asyncio.run(export_snapshot(app))
    app.state.db.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size // 1024} KB). Once pushed, this is public:")
    for line in summary(snapshot):
        print(f"  - {line}")


if __name__ == "__main__":
    main()
