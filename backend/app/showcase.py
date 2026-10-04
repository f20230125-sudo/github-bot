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
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from .agents.linkedin import PITCH
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
    "/api/agents/pitch",
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


def _settled(repo: dict[str, Any]) -> dict[str, Any]:
    """"CI running" is true for a few minutes and a snapshot is read for hours: say nothing instead."""
    return {**repo, "ci_state": None} if repo.get("ci_state") == "pending" else repo


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
        routes["/api/repos"]["repos"] = [_settled(repo) for repo in routes["/api/repos"]["repos"]]
        for repo in routes["/api/repos"]["repos"]:
            path = f"/api/repos/{repo['owner']}/{repo['name']}"
            routes[path] = await get(path)
            routes[path]["repo"] = _settled(routes[path]["repo"])
        for proposal in routes["/api/proposals?status=all"]["proposals"]:
            path = f"/api/proposals/{proposal['id']}"
            routes[path] = await get(path)
        runs = routes["/api/runs?limit=100"]
        runs["runs"] = [run for run in runs["runs"] if not _private_run(run["agent"], run["run_id"])]
        for run in runs["runs"]:
            path = f"/api/runs/{run['run_id']}"
            routes[path] = await get(path)
        events = (await get(f"/api/events?limit={MAX_EVENTS}"))["events"]

    # Pitch's drafts never leave the desk. Nor does anything that came of them: the runs that
    # wrote them, the rules learned from them (a rule can repeat what a draft said), the tone
    # you chose, or a status line saying a draft is waiting. The posts themselves are not among
    # the pages asked for at all. What stays is Pitch reading Patch's notes.
    events = [e for e in events if not _private_run(e["agent"], e["run_id"])]
    reading = app.state.pitch.public_status()
    routes["/api/agents/pitch"] |= {"lessons": [], "tone": None, "status": reading}

    desk = routes["/api/agents"]
    desk["agents"] = [_without_clock(card) for card in desk["agents"]]
    for card in desk["agents"]:
        if card["id"] == PITCH.id:
            card["status"] = reading
    desk["current"] = None
    routes["/api/agents/patch"] = _without_clock(routes["/api/agents/patch"])
    routes["/api/chat?limit=30"]["busy"] = False
    return {"exported_at": utcnow(), "events": _latest_work(events), "routes": routes}


def _private_run(agent: str, run_id: str | None) -> bool:
    """Whether an event or a run is Pitch's and about anything other than reading Patch's notes."""
    return agent == PITCH.id and not (run_id or "").startswith("read-")


def _latest_work(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events the site replays: from the most recent audit that looked at a repository, onward.

    Older runs stay reachable through their own pages. Replaying all of them on every visit would
    bury the newest one.
    """
    audits = [e["run_id"] for e in events if e["type"] == "run.started" and e["payload"].get("job") == "audit"]
    looked = {e["run_id"] for e in events if e["type"] == "run.step" and e["payload"].get("step") == "repo"}
    recent = [run_id for run_id in audits if run_id in looked] or audits
    if not recent:
        return events
    start = next(i for i, e in enumerate(events) if e["run_id"] == recent[-1])
    # Keep the "working" status that was published just before the run began.
    if start > 0 and events[start - 1]["type"] == "agent.status":
        start -= 1
    return events[start:]


def digest(snapshot: dict[str, Any]) -> str:
    """A fingerprint of what the site shows that is worth a new deployment: scores, findings,
    proposals, lessons, notes for Pitch with its verdict on each, and Patch's mood. Clock times,
    request counts and star counts don't count, so a check that changes none of these leaves the
    published file alone.

    Nor does a CI run starting or finishing. CI that begins to fail, or passes again, arrives as a
    finding. What is left is "running" turning into "passing", and the job's own commit, which no
    CI runs on: counting those would publish again after every publish."""
    routes = snapshot["routes"]
    repos = [
        (repo["full_name"], repo["kind"], repo["score"], repo["counts"], repo["description"], repo["topics"],
         repo["homepage"], repo["license"])
        for repo in routes["/api/repos"]["repos"]
    ]  # fmt: skip
    findings = {
        path: sorted((f["check"], f["severity"], f["title"]) for f in answer["findings"])
        for path, answer in routes.items()
        if path.startswith("/api/repos/")
    }
    # Only what is waiting, and only what it would change: a redraft with the same content is not news.
    proposals = sorted(
        (
            answer["kind"],
            answer["repo"] or "",
            answer["title"],
            [(f["path"], f["content"], f["enabled"]) for f in answer["payload"].get("files", [])],
            [(i["repo"], i["description"], i["topics"], i["enabled"]) for i in answer["payload"].get("items", [])],
        )
        for path, answer in routes.items()
        if path.startswith("/api/proposals/") and answer["status"] == "pending"
    )
    lessons = [(lesson["text"], lesson["active"]) for lesson in routes["/api/agents/patch"]["lessons"]]
    # A note for Pitch, and whether Pitch finds enough in it for a post.
    handoffs = [(handoff["text"], handoff["brief"]["ready"]) for handoff in routes["/api/handoffs"]["handoffs"]]
    mood = routes["/api/agents/patch"]["mood"]  # the face Patch wears: it follows the portfolio score
    # What Pitch says it does and never does. It changes only when the code does, and then the
    # site should say so without waiting for a repository to change.
    pitch = (routes["/api/agents/pitch"]["does"], routes["/api/agents/pitch"]["never"])
    substance = json.dumps([repos, findings, proposals, lessons, handoffs, mood, pitch], sort_keys=True, default=str)
    return hashlib.sha256(substance.encode()).hexdigest()[:16]


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
        f"{len(routes['/api/handoffs']['handoffs'])} notes for Pitch, with what a post may state about each "
        "(no draft, no tone and no rule of Pitch's: those stay on this machine)",
        f"{len(routes['/api/agents/patch']['lessons'])} lessons",
        "your Claude plan usage percentages" if reported else "no Claude plan usage figures",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a view-only snapshot for the public demo site.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write the snapshot")
    args = parser.parse_args()

    app = create_app(get_settings())
    snapshot = asyncio.run(export_snapshot(app))
    snapshot["digest"] = digest(snapshot)
    app.state.db.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {args.out} ({args.out.stat().st_size // 1024} KB). Once pushed, this is public:")
    for line in summary(snapshot):
        print(f"  - {line}")


if __name__ == "__main__":
    main()
