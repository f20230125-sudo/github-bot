"""Looking back: what the desk has used, how its proposals fared, and every run it has made."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..db import Database
from ..events import Event

router = APIRouter(prefix="/api")

DAILY_KEYS = (
    "runs", "checks", "idle_checks", "github_requests", "github_not_modified", "claude_calls",
    "input_tokens", "output_tokens", "cache_read_tokens", "calls_avoided", "handoffs",
)  # fmt: skip


def _by_job(days: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
    """Model calls and tokens for each kind of job, most calls first."""
    jobs: dict[str, dict[str, Any]] = {}
    for totals in days.values():
        for key, value in totals.items():
            field, _, job = key.partition(".")
            if job and field in ("calls", "tokens_in", "tokens_out"):
                entry = jobs.setdefault(job, {"job": job, "calls": 0, "tokens_in": 0, "tokens_out": 0})
                entry[field] += value
    return sorted(jobs.values(), key=lambda entry: (-entry["calls"], entry["job"]))


def _decisions(state: Any, since_day: str) -> dict[str, Any]:
    """How your decisions on Patch's proposals went."""
    by_day: dict[str, dict[str, int]] = {}
    approved = rejected = edited = 0
    for proposal in state.proposals.list():
        decision = proposal.decision
        if not decision:
            continue
        ok = decision.get("action") == "approve"
        approved += ok
        rejected += not ok
        edited += bool(ok and decision.get("edits"))
        day = str(decision.get("at", ""))[:10]
        if day >= since_day:
            counts = by_day.setdefault(day, {"day": day, "approved": 0, "rejected": 0})
            counts["approved" if ok else "rejected"] += 1
    decided = approved + rejected
    return {
        "approved": approved,
        "rejected": rejected,
        "edited": edited,  # approved, but only after you changed something
        "pending": len(state.proposals.list("pending")),
        "rate": round(100 * approved / decided) if decided else None,
        "by_day": [by_day[day] for day in sorted(by_day)],
    }


@router.get("/metrics")
async def metrics(request: Request, days: int = Query(14, ge=1, le=90)):
    state = request.app.state
    today = date.today()
    since = today - timedelta(days=days - 1)
    stored = state.db.daily_totals(since.isoformat())

    series = []
    for offset in range(days):
        day = (since + timedelta(days=offset)).isoformat()
        series.append({"day": day, **{key: stored.get(day, {}).get(key, 0) for key in DAILY_KEYS}})
    totals = {key: sum(day[key] for day in series) for key in DAILY_KEYS}

    guard = state.claude.guard
    return {
        "days": series,
        "today": series[-1],
        "totals": totals,
        "by_job": _by_job(stored),
        "decisions": _decisions(state, since.isoformat()),
        "usage": guard.snapshot() | {"readings": guard.readings(days=days)},
    }


# -- runs -------------------------------------------------------------------------------------


def _summary(started: Event, others: list[Event]) -> dict[str, Any]:
    finished = next((e for e in others if e.type == "run.finished"), None)
    usage = next((e for e in others if e.type == "usage"), None)
    return {
        "run_id": started.run_id,
        "agent": started.agent,
        "job": started.payload.get("job"),
        "title": started.payload.get("title") or started.run_id,
        "demo": started.payload.get("demo") is True,
        "started_at": started.ts,
        "finished_at": finished.ts if finished else None,
        "ok": finished.payload.get("ok") if finished else None,  # None: still running, or it was cut off
        "text": finished.payload.get("text") if finished else None,
        "usage": usage.payload if usage else {},
    }


def run_summaries(db: Database, limit: int, job: str | None = None) -> list[dict[str, Any]]:
    """The newest runs, newest first."""
    clause, params = "", []
    if job:
        clause = "AND json_extract(payload, '$.job') = ?"
        params.append(job)
    started = [
        db.row_to_event(row)
        for row in db.query(
            f"SELECT * FROM events WHERE type = 'run.started' {clause} ORDER BY id DESC LIMIT ?", (*params, limit)
        )
    ]
    if not started:
        return []
    marks = ", ".join("?" for _ in started)
    rows = db.query(
        f"SELECT * FROM events WHERE run_id IN ({marks}) AND type IN ('run.finished', 'usage')",
        tuple(e.run_id for e in started),
    )
    by_run: dict[str, list[Event]] = {}
    for row in rows:
        by_run.setdefault(row["run_id"], []).append(db.row_to_event(row))
    return [_summary(event, by_run.get(event.run_id or "", [])) for event in started]


@router.get("/runs")
async def list_runs(request: Request, limit: int = Query(30, ge=1, le=200), job: str | None = None):
    return {"runs": run_summaries(request.app.state.db, limit, job)}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str):
    events = request.app.state.db.events_for_run(run_id)
    started = next((e for e in events if e.type == "run.started"), None)
    if started is None:
        raise HTTPException(404, "No such run.")
    return {"run": _summary(started, events), "events": [e.model_dump() for e in events]}
