"""Helpers for tests that need Claude: they point the app at the stand-in CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from app.core.claude import Claude
from app.core.claude_cli import ClaudeCli
from app.core.usage_guard import UsageGuard

FAKE = Path(__file__).parent / "fake_claude.py"
ZERO_USAGE = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

# What the real CLI is expected to say when it is under the limit, with its 5-hour figure.
UNDER_LIMIT = {"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.12, "resetsAt": 4102444800}
USAGE_TEXT = "Current session\n12% used\nResets 3:59pm\n\nCurrent week (all models)\n30% used\nResets Oct 7"


class FakeClaude:
    """Writes a scenario file and reads back what the stand-in was called with."""

    def __init__(self, tmp_path: Path, scenario: dict[str, Any] | None = None):
        self.path = tmp_path / "claude-scenario.json"
        self.cwd = tmp_path / "claude-cwd"
        self.set(scenario or {})

    def set(self, scenario: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(scenario), encoding="utf-8")
        for counter in self.path.parent.glob(self.path.name + ".*.count"):
            counter.unlink()

    @property
    def command(self) -> list[str]:
        return [sys.executable, str(FAKE), str(self.path)]

    def calls(self) -> list[dict[str, Any]]:
        log = Path(str(self.path) + ".calls.jsonl")
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]

    def cli(self, timeout: float = 20.0) -> ClaudeCli:
        return ClaudeCli(self.command, self.cwd, timeout=timeout)

    def service(self, db, **guard_options: Any) -> Claude:
        return Claude(self.cli(), UsageGuard(db, **guard_options), small_model="haiku")


def flag(call: dict[str, Any], name: str) -> str:
    """The value that follows a flag in a recorded call."""
    return call["args"][call["args"].index(name) + 1]
