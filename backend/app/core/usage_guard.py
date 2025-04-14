"""The stop on Claude usage.

Patch calls Claude only while the plan's usage is under the limits you set (40% by default) on
both the 5-hour window and the weekly one. The figure covers the whole account, so your own
Claude use counts toward it.

It fails closed: when no percentage can be read, Claude is not called. The one exception is a
fixed allowance you can opt into, which bounds the number of calls instead.

Readings come from Claude Code itself, from two places:
- the rate-limit report that accompanies a call, and
- the text of its `/usage` command.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import Database

KV_KEY = "usage_guard"
LOG_EVERY_SECONDS = 600
SESSION_SECONDS = 5 * 3600
WEEK_SECONDS = 7 * 86400
WINDOW_SECONDS = {"session": SESSION_SECONDS, "weekly": WEEK_SECONDS}
JUST_BEFORE_SECONDS = 300  # a usage check this long before a window's first call still describes that window
WINDOW_LABEL = {"session": "5-hour", "weekly": "weekly"}

# How Claude Code names the windows in its rate-limit reports.
_WINDOW_NAMES = {
    "five_hour": "session", "5h": "session", "session": "session",
    "seven_day": "weekly", "seven_day_opus": "weekly", "seven_day_sonnet": "weekly", "7d": "weekly",
    "weekly": "weekly",
}  # fmt: skip
_STOP_STATUSES = {"rejected", "allowed_warning"}
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SHARE_RE = re.compile(r"%\s*of\b")
_SESSION_RE = re.compile(r"session|5[\s-]?hour|\b5h\b")
_WEEK_RE = re.compile(r"week|7[\s-]?day|\b7d\b")
# "resets Oct 4, 9:20pm (Asia/Dubai)", "Resets 3:59pm": what follows the word, and the zone if named.
_RESET_RE = re.compile(r"resets?\s+(?:at\s+|on\s+)?([^()\n]+?)\s*(?:\(([^)]+)\))?\s*$", re.I)
_WHEN_RE = re.compile(
    r"^(?:(?P<month>[a-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2})(?:,|\s|$)\s*)?"
    r"(?:(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<half>am|pm)?)?$",
    re.I,
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
    )
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str  # ok, allowance, over_limit, limited, no_reading, stale, allowance_spent
    reason: str


def _zone(name: str | None) -> tzinfo | None:
    """The named time zone, or this machine's when there is no name or this machine doesn't know
    it (Windows ships no zone database). Claude Code reports times in the machine's own zone."""
    if name:
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def parse_reset(when: str, zone: str | None, now: float) -> float | None:
    """When a window resets, from "Oct 4, 9:20pm" or "3:59pm", as seconds since the epoch.
    None if the text can't be read, or names a day without a time."""
    match = _WHEN_RE.match(when.strip())
    if not match or match["hour"] is None:
        return None
    hour, minute, half = int(match["hour"]), int(match["minute"] or 0), (match["half"] or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None

    base = datetime.fromtimestamp(now, _zone(zone))
    try:
        if match["month"]:
            month = _MONTHS.get(match["month"].lower()[:3])
            if month is None:
                return None
            moment = base.replace(month=month, day=int(match["day"]), hour=hour, minute=minute, second=0, microsecond=0)
            if moment < base - timedelta(days=1):
                moment = moment.replace(year=moment.year + 1)  # "Jan 2", read in late December
        else:
            moment = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if moment <= base:
                moment += timedelta(days=1)  # a time of day that has passed means tomorrow
    except ValueError:
        return None  # a day that doesn't exist
    return moment.timestamp()


def parse_usage(text: str, now: float | None = None) -> dict[str, dict[str, float | None]]:
    """Read `/usage` output: for each window, how full it is and, where the text says so, when it
    resets. Both layouts are understood: "Current session: 69% used · resets Oct 4, 9:20pm
    (Asia/Dubai)" on one line, and the figure and the reset on the lines below the heading.
    Unrecognised text gives an empty result."""
    now = time.time() if now is None else now
    lines = text.splitlines()
    found: dict[str, dict[str, float | None]] = {}
    for i, line in enumerate(lines):
        lower = line.lower()
        if lower.lstrip().startswith("last "):
            continue  # "Last 24h · 433 requests · 2 sessions": a breakdown of past use, not a limit
        window = "session" if _SESSION_RE.search(lower) else "weekly" if _WEEK_RE.search(lower) else None
        if window is None:
            continue
        # The window's own lines: this one and up to two below, stopping where another window starts.
        block = [line]
        for candidate in lines[i + 1 : i + 3]:
            if _SESSION_RE.search(candidate.lower()) or _WEEK_RE.search(candidate.lower()):
                break
            block.append(candidate)

        percent = None
        for candidate in block:
            match = _PERCENT_RE.search(candidate)
            if match and not _SHARE_RE.search(candidate):  # "96% of your usage was ..." is a share, not a level
                percent = float(match.group(1))
                if re.search(r"left|remaining", candidate.lower()):
                    percent = 100 - percent
                break
        if percent is None:
            continue
        resets = next((m for m in map(_RESET_RE.search, block) if m), None)
        # Several weekly lines (all models, one model): keep the highest. First session line wins.
        if window not in found or (window == "weekly" and percent > (found[window]["percent"] or 0)):
            found[window] = {
                "percent": percent,
                "resets_at": parse_reset(resets.group(1), resets.group(2), now) if resets else None,
            }
    return found


def parse_usage_text(text: str) -> dict[str, float]:
    """Just the "12% used" style figures out of `/usage` output, by window."""
    return {window: reading["percent"] for window, reading in parse_usage(text).items()}  # type: ignore[misc]


class UsageGuard:
    def __init__(
        self,
        db: Database,
        session_limit: float = 40.0,
        weekly_limit: float = 40.0,
        fresh_seconds: float = 1800.0,
        fixed_allowance: int = 0,
        clock: Callable[[], float] = time.time,
    ):
        self.db = db
        self.limits = {"session": session_limit, "weekly": weekly_limit}
        self.fresh_seconds = fresh_seconds
        self.fixed_allowance = fixed_allowance
        self._clock = clock

    # -- state --------------------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        raw = self.db.get_kv(KV_KEY)
        state = json.loads(raw) if raw else {}
        state.setdefault("readings", {})
        state.setdefault("blocked", None)
        state.setdefault("usage_command", None)  # "local" when /usage answers without a model call
        state.setdefault("percent_scale", False)
        state.setdefault("calls", [])
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self.db.set_kv(KV_KEY, json.dumps(state))

    def _log(self, window: str, percent: float, now: float) -> None:
        """Keep the reading for the metrics page. A figure that hasn't moved is logged every ten minutes at most."""
        last = self.db.query_one(
            "SELECT ts, percent FROM usage_readings WHERE scope = ? ORDER BY id DESC LIMIT 1", (window,)
        )
        if last and last["percent"] == percent and now - _from_iso(last["ts"]) < LOG_EVERY_SECONDS:
            return
        self.db.execute(
            "INSERT INTO usage_readings (ts, scope, percent) VALUES (?, ?, ?)", (_iso(now), window, percent)
        )

    def readings(self, days: int = 7) -> list[dict[str, Any]]:
        """Usage figures from the last `days` days, oldest first."""
        since = _iso(self._clock() - days * 86400)
        rows = self.db.query("SELECT ts, scope, percent FROM usage_readings WHERE ts >= ? ORDER BY id", (since,))
        return [{"ts": row["ts"], "window": row["scope"], "percent": row["percent"]} for row in rows]

    @property
    def usage_command(self) -> str | None:
        return self._load()["usage_command"]

    def set_usage_command(self, value: str) -> None:
        state = self._load()
        state["usage_command"] = value
        self._save(state)

    # -- recording ----------------------------------------------------------------------------

    def record_events(self, infos: list[dict[str, Any]]) -> None:
        """Take in the rate-limit reports that came with a call."""
        if not infos:
            return
        state = self._load()
        now = self._clock()
        for info in infos:
            status = str(info.get("status") or "allowed")
            window = _WINDOW_NAMES.get(str(info.get("rateLimitType") or info.get("rate_limit_type") or "").lower())
            resets_at = _epoch(info.get("resetsAt") or info.get("resets_at"))

            percent = None
            utilization = info.get("utilization")
            if isinstance(utilization, int | float) and not isinstance(utilization, bool):
                # Claude Code reports a fraction (0.23 = 23%). If a value above 1 ever shows up,
                # the scale is percent, and it is treated that way from then on.
                if utilization > 1.5:
                    state["percent_scale"] = True
                percent = float(utilization) if state["percent_scale"] else float(utilization) * 100

            if status in _STOP_STATUSES:
                state["blocked"] = {"status": status, "at": now, "until": resets_at or now + SESSION_SECONDS}

            key = window or "session"  # an unnamed window is treated as the 5-hour one
            previous = state["readings"].get(key) or {}
            if percent is not None:
                state["readings"][key] = {"percent": percent, "resets_at": resets_at, "at": now, "source": "call"}
                self._log(key, percent, now)
            elif window and resets_at:
                # A report with no figure. It still tells us when the window resets. Keep the last
                # figure only if it was taken in this same window: after a reset it no longer holds.
                same_window = _read_in_window(previous, resets_at, WINDOW_SECONDS[key])
                state["readings"][key] = {
                    "percent": previous.get("percent") if same_window else None,
                    "resets_at": resets_at,
                    "at": previous.get("at", now) if same_window else now,
                    "source": previous.get("source", "call") if same_window else "call",
                }
        self._save(state)

    def record_usage_text(self, text: str) -> bool:
        """Take in `/usage` output. Returns whether any figure could be read."""
        now = self._clock()
        figures = parse_usage(text, now)
        if not figures:
            return False
        state = self._load()
        for window, reading in figures.items():
            previous = state["readings"].get(window, {})
            known = previous.get("resets_at") if (previous.get("resets_at") or 0) > now else None
            percent = reading["percent"]
            state["readings"][window] = {
                "percent": percent,
                # When the text says when the window resets, that is what counts. Otherwise the
                # last reset time we were told, while it is still ahead.
                "resets_at": reading["resets_at"] or known,
                "at": now,
                "source": "usage command",
            }
            self._log(window, percent, now)
        self._save(state)
        return True

    def seconds_since_reading(self) -> float:
        """Age of the newest percentage we hold, or infinity if there is none."""
        ages = [self._clock() - r["at"] for r in self._load()["readings"].values() if r.get("percent") is not None]
        return min(ages) if ages else float("inf")

    def note_call(self) -> None:
        """Count a model call, for the fixed allowance."""
        state = self._load()
        now = self._clock()
        state["calls"] = [t for t in state["calls"] if now - t < SESSION_SECONDS] + [now]
        self._save(state)

    # -- deciding -----------------------------------------------------------------------------

    def _valid_readings(self, state: dict[str, Any], now: float) -> dict[str, dict[str, Any]]:
        """Readings with a percentage whose window hasn't reset since they were taken."""
        valid = {}
        for window, reading in state["readings"].items():
            if reading.get("percent") is None:
                continue
            resets_at = reading.get("resets_at")
            if resets_at:
                expired = now >= resets_at
            else:  # no reset time known: trust the figure for at most one full window
                expired = now - reading["at"] >= WINDOW_SECONDS.get(window, SESSION_SECONDS)
            if not expired:
                valid[window] = reading
        return valid

    def decide(self) -> Decision:
        state = self._load()
        now = self._clock()

        blocked = state["blocked"]
        if blocked and now < blocked["until"]:
            what = "a rate-limit warning" if blocked["status"] == "allowed_warning" else "that the limit is reached"
            until = _clock_time(blocked["until"])
            return Decision(False, "limited", f"Claude Code reported {what}. Waiting until {until}.")

        readings = self._valid_readings(state, now)
        for window in ("session", "weekly"):
            reading = readings.get(window)
            if reading and reading["percent"] >= self.limits[window]:
                until = f" It resets at {_clock_time(reading['resets_at'])}." if reading.get("resets_at") else ""
                return Decision(
                    False,
                    "over_limit",
                    f"Your {WINDOW_LABEL[window]} usage is {reading['percent']:.0f}%, "
                    f"at or above the {self.limits[window]:.0f}% stop.{until}",
                )

        if not readings:
            if self.fixed_allowance > 0:
                used = len([t for t in state["calls"] if now - t < SESSION_SECONDS])
                total = self.fixed_allowance
                if used < total:
                    return Decision(True, "allowance", f"Fixed allowance: {used} of {total} calls used.")
                return Decision(False, "allowance_spent", f"All {total} calls of the fixed allowance are used.")
            return Decision(False, "no_reading", "Patch can't read your plan usage, so it won't call Claude.")

        newest = max(reading["at"] for reading in readings.values())
        if now - newest > self.fresh_seconds:
            return Decision(False, "stale", f"The last usage reading is {int((now - newest) // 60)} minutes old.")

        top = max(readings.items(), key=lambda item: item[1]["percent"] / self.limits.get(item[0], 40.0))
        return Decision(
            True,
            "ok",
            f"{WINDOW_LABEL.get(top[0], top[0]).capitalize()} usage is {top[1]['percent']:.0f}%, "
            f"under the {self.limits.get(top[0], 40.0):.0f}% stop.",
        )

    def snapshot(self) -> dict[str, Any]:
        """Everything the Setup page shows about the guard."""
        state = self._load()
        now = self._clock()
        valid = self._valid_readings(state, now)
        decision = self.decide()
        return {
            "allowed": decision.allowed,
            "code": decision.code,
            "reason": decision.reason,
            "limits": self.limits,
            "fixed_allowance": self.fixed_allowance,
            "usage_command": state["usage_command"],
            "windows": {
                window: {
                    "percent": valid[window]["percent"] if window in valid else None,
                    "resets_at": valid[window].get("resets_at") if window in valid else None,
                    "minutes_old": int((now - valid[window]["at"]) // 60) if window in valid else None,
                    "source": valid[window]["source"] if window in valid else None,
                }
                for window in ("session", "weekly")
            },
        }


def _read_in_window(previous: dict[str, Any], resets_at: float, length: float) -> bool:
    """Whether the figure we hold was read in the window that ends at `resets_at`."""
    known = previous.get("resets_at")
    if known:
        return abs(known - resets_at) < 120
    # The figure came without a reset time, as "0% used" does before a window has begun. It is
    # this window's figure if it was read after the window began, or just before the call that
    # began it. A window begins with the first call after the last one ran out.
    began = resets_at - length
    return previous.get("percent") is not None and previous.get("at", 0) > began - JUST_BEFORE_SECONDS


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _from_iso(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def _epoch(value: Any) -> float | None:
    """Seconds since the epoch, from seconds or milliseconds."""
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return None
    return float(value) / 1000 if value > 1e11 else float(value)


def _clock_time(epoch: float | None) -> str:
    if not epoch:
        return "the next reset"
    moment = datetime.fromtimestamp(epoch)
    return moment.strftime("%H:%M") if moment.date() == datetime.now().date() else moment.strftime("%d %b %H:%M")
