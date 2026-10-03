"""What an agent may do without asking, and whether it writes at all.

Everything starts on "ask" with dry-run on: an agent proposes, you decide, and even then nothing
is written until you switch dry-run off.
"""

from __future__ import annotations

import json
from typing import Any

from ..db import Database

KV_KEY = "policy"
MODES = ("ask", "auto", "never")
# The kinds of proposal an agent can make, and what "auto" would let it do unasked.
KINDS = {
    "metadata_sweep": "Set repository descriptions and topics",
    "pull_request": "Open pull requests with file changes",
}


class PolicyError(ValueError):
    pass


class Policy:
    def __init__(self, db: Database, dry_run_default: bool = True):
        self._db = db
        self._dry_run_default = dry_run_default

    def get(self) -> dict[str, Any]:
        stored = json.loads(self._db.get_kv(KV_KEY) or "{}")
        return {
            "dry_run": stored.get("dry_run", self._dry_run_default),
            "merge_after_approval": stored.get("merge_after_approval", False),
            "kinds": {kind: stored.get("kinds", {}).get(kind, "ask") for kind in KINDS},
        }

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        policy = self.get()
        for key, value in changes.items():
            if key in ("dry_run", "merge_after_approval"):
                if not isinstance(value, bool):
                    raise PolicyError(f"{key} must be true or false.")
                policy[key] = value
            elif key == "kinds":
                for kind, mode in dict(value).items():
                    if kind not in KINDS or mode not in MODES:
                        raise PolicyError(f"Unknown setting: {kind} = {mode}.")
                    policy["kinds"][kind] = mode
            else:
                raise PolicyError(f"Unknown setting: {key}.")
        self._db.set_kv(KV_KEY, json.dumps(policy))
        return policy

    @property
    def dry_run(self) -> bool:
        return bool(self.get()["dry_run"])

    def mode(self, kind: str) -> str:
        return self.get()["kinds"].get(kind, "ask")
