from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date
from pathlib import Path

from .events import Event, NewEvent, redact, utcnow

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


class Database:
    """SQLite (WAL) behind a lock. Writes are tiny, so the calls are made directly."""

    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run one write statement and commit. Returns the last inserted row id."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid or 0

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def bump_stats(self, agent: str, counters: dict[str, int], day: str | None = None) -> None:
        """Add to today's totals."""
        day = day or date.today().isoformat()
        with self._lock:
            for key, value in counters.items():
                if value:
                    self._conn.execute(
                        "INSERT INTO daily_stats (day, agent, key, value) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(day, agent, key) DO UPDATE SET value = value + excluded.value",
                        (day, agent, key, int(value)),
                    )
            self._conn.commit()

    def daily_stats(self, agent: str, days: int = 30) -> list[dict[str, int | str]]:
        """One row per day, oldest first, each with that day's totals."""
        rows = self.query(
            "SELECT day, key, value FROM daily_stats WHERE agent = ? AND day IN "
            "(SELECT DISTINCT day FROM daily_stats WHERE agent = ? ORDER BY day DESC LIMIT ?) ORDER BY day ASC",
            (agent, agent, days),
        )
        by_day: dict[str, dict[str, int | str]] = {}
        for row in rows:
            by_day.setdefault(row["day"], {"day": row["day"]})[row["key"]] = row["value"]
        return list(by_day.values())

    def daily_totals(self, since_day: str) -> dict[str, dict[str, int]]:
        """Every agent's totals added together, by day, from `since_day` (YYYY-MM-DD) on."""
        rows = self.query(
            "SELECT day, key, SUM(value) AS value FROM daily_stats WHERE day >= ? GROUP BY day, key", (since_day,)
        )
        by_day: dict[str, dict[str, int]] = {}
        for row in rows:
            by_day.setdefault(row["day"], {})[row["key"]] = int(row["value"])
        return by_day

    def get_kv(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def insert_event(self, ev: NewEvent) -> Event:
        ts = utcnow()
        payload = redact(ev.payload)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, agent, type, run_id, repo, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, ev.agent, ev.type, ev.run_id, ev.repo, json.dumps(payload, sort_keys=True)),
            )
            self._conn.commit()
            event_id = cur.lastrowid
        return Event(
            id=event_id, ts=ts, agent=ev.agent, type=ev.type, run_id=ev.run_id, repo=ev.repo, payload=payload
        )

    def events_after(self, after_id: int, limit: int = 500) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?", (after_id, limit)
            ).fetchall()
        return [_row_to_event(r) for r in rows]

    def latest(self, limit: int = 100) -> list[Event]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_event(r) for r in reversed(rows)]

    def events_for_run(self, run_id: str) -> list[Event]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM events WHERE run_id = ? ORDER BY id ASC", (run_id,)).fetchall()
        return [_row_to_event(r) for r in rows]

    @staticmethod
    def row_to_event(row: sqlite3.Row) -> Event:
        return _row_to_event(row)

    def latest_of(self, agent: str, type_: str) -> Event | None:
        """An agent's most recent event of one type."""
        row = self.query_one(
            "SELECT * FROM events WHERE agent = ? AND type = ? ORDER BY id DESC LIMIT 1", (agent, type_)
        )
        return _row_to_event(row) if row else None

    def messages(self, kind: str, limit: int = 50) -> list[Event]:
        """The newest `limit` messages of one kind ("chat", "handoff", "say"), oldest first."""
        rows = self.query(
            "SELECT * FROM events WHERE type = 'message' AND json_extract(payload, '$.kind') = ? "
            "ORDER BY id DESC LIMIT ?",
            (kind, limit),
        )
        return [_row_to_event(row) for row in reversed(rows)]

    def last_id(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM events").fetchone()
        return int(row["n"])


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        ts=row["ts"],
        agent=row["agent"],
        type=row["type"],
        run_id=row["run_id"],
        repo=row["repo"],
        payload=json.loads(row["payload"]),
    )
