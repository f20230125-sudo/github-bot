from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low", "info"]
FixKind = Literal["metadata", "file", "manual"]
RepoKind = Literal["project", "profile", "placeholder", "skipped"]
CiState = Literal["success", "failure", "pending", "neutral"]


class RepoMeta(BaseModel):
    """What GitHub's repository list tells us. One request covers every repository."""

    full_name: str
    name: str
    owner: str
    html_url: str | None = None
    description: str | None = None
    homepage: str | None = None
    topics: list[str] = Field(default_factory=list)
    license: str | None = None  # SPDX id as GitHub detected it
    default_branch: str = "main"
    pushed_at: str | None = None
    private: bool = False
    archived: bool = False
    fork: bool = False
    stars: int = 0
    open_issues: int = 0
    language: str | None = None
    size_kb: int = 0

    def fingerprint(self) -> str:
        """Changes only when something the checks read changes (stars and issue counts don't count)."""
        fields = {
            "description": self.description,
            "homepage": self.homepage,
            "topics": sorted(self.topics),
            "license": self.license,
            "default_branch": self.default_branch,
            "private": self.private,
            "archived": self.archived,
            "fork": self.fork,
        }
        return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


class RepoDetails(BaseModel):
    """What needs a look inside the repository: its files, README and CI state."""

    empty: bool = False
    head_sha: str | None = None
    tree_sha: str | None = None
    files: list[str] = Field(default_factory=list)
    dirs: list[str] = Field(default_factory=list)
    truncated: bool = False
    # None means the whole tree was listed; a number means only that many levels were.
    tree_depth: int | None = None
    readme_path: str | None = None
    readme_text: str | None = None
    ci_state: CiState | None = None
    # The newest release's tag and when it was published. Only read with a token (GraphQL).
    latest_release: str | None = None
    released_at: str | None = None


class RepoSnapshot(BaseModel):
    meta: RepoMeta
    details: RepoDetails


class Finding(BaseModel):
    check: str
    severity: Severity
    title: str  # neutral statement, safe to show anywhere
    detail: str  # the evidence: what was looked at
    fix: FixKind | None = None  # how it can be fixed, if Patch can help
    weight: int = 0  # points off the score
    data: dict[str, str | int] = Field(default_factory=dict)  # values for the voice line


class RepoReport(BaseModel):
    full_name: str
    kind: RepoKind
    snapshot: RepoSnapshot
    findings: list[Finding]
    score: int | None
