"""Find out what changed on GitHub and look inside only those repositories.

Two ways to look inside:
- GraphQL (needs a token): one query covers up to ten repositories.
- REST (works without a token on public repositories): up to three requests per repository.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from .client import AuthError, Conflict, GitHubClient, GitHubError, NotFound, PermissionDenied, RateLimited
from .models import CiState, RepoDetails, RepoMeta, RepoSnapshot
from .paths import WORKFLOW_RE, find_readme

GRAPHQL_CHUNK = 10
GRAPHQL_TREE_DEPTH = 4
# How long after a push a CI run that hasn't finished is still worth re-checking.
PENDING_WINDOW = timedelta(hours=2)


# -- the repository list ----------------------------------------------------------------------


def meta_from_rest(item: Mapping[str, Any]) -> RepoMeta:
    license_info = item.get("license") or {}
    return RepoMeta(
        full_name=item["full_name"],
        name=item["name"],
        owner=item["owner"]["login"],
        html_url=item.get("html_url"),
        description=(item.get("description") or "").strip() or None,
        homepage=(item.get("homepage") or "").strip() or None,
        topics=list(item.get("topics") or []),
        license=license_info.get("spdx_id") or None,
        default_branch=item.get("default_branch") or "main",
        pushed_at=item.get("pushed_at"),
        private=bool(item.get("private")),
        archived=bool(item.get("archived")),
        fork=bool(item.get("fork")),
        stars=int(item.get("stargazers_count") or 0),
        open_issues=int(item.get("open_issues_count") or 0),
        language=item.get("language"),
        size_kb=int(item.get("size") or 0),
    )


async def list_repos(client: GitHubClient, user: str) -> tuple[list[RepoMeta], bool]:
    """Every repository the user owns, and whether GitHub said the list is unchanged."""
    if client.authenticated:
        path, params = "/user/repos", {"affiliation": "owner", "per_page": 100, "sort": "full_name"}
    else:
        path, params = f"/users/{user}/repos", {"type": "owner", "per_page": 100, "sort": "full_name"}
    items, not_modified = await client.get_all(path, params)
    return [meta_from_rest(item) for item in items], not_modified


# -- deciding what to fetch -------------------------------------------------------------------


@dataclass(frozen=True)
class Known:
    """What we stored about a repository last time."""

    meta_fp: str
    pushed_at: str | None
    snapshot: RepoSnapshot


@dataclass
class SyncPlan:
    need_details: list[RepoMeta] = field(default_factory=list)  # look inside again
    meta_only: list[RepoMeta] = field(default_factory=list)  # only the listing changed
    unchanged: list[RepoMeta] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _ci_still_running(old: Known, meta: RepoMeta, now: datetime) -> bool:
    """A CI run was going at the last look. Look again, unless the push is so old the run is stuck:
    then every check would re-read the repository for nothing."""
    if old.snapshot.details.ci_state != "pending":
        return False
    try:
        pushed = datetime.fromisoformat((meta.pushed_at or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - pushed < PENDING_WINDOW


def plan_sync(listed: list[RepoMeta], known: Mapping[str, Known], now: datetime | None = None) -> SyncPlan:
    now = now or datetime.now(UTC)
    plan = SyncPlan()
    for meta in listed:
        old = known.get(meta.full_name)
        skipped = meta.fork or meta.archived  # listed, never looked inside
        if old is None:
            (plan.meta_only if skipped else plan.need_details).append(meta)
        elif skipped:
            (plan.meta_only if old.meta_fp != meta.fingerprint() else plan.unchanged).append(meta)
        elif (
            old.pushed_at != meta.pushed_at
            or old.snapshot.meta.default_branch != meta.default_branch
            or old.snapshot.meta.fork
            or old.snapshot.meta.archived
            or _ci_still_running(old, meta, now)
        ):
            plan.need_details.append(meta)
        elif old.meta_fp != meta.fingerprint():
            plan.meta_only.append(meta)
        else:
            plan.unchanged.append(meta)
    listed_names = {m.full_name for m in listed}
    plan.removed = sorted(name for name in known if name not in listed_names)
    return plan


# -- looking inside: REST ---------------------------------------------------------------------

_RUN_CONCLUSIONS: dict[str, CiState] = {
    "success": "success",
    "failure": "failure",
    "timed_out": "failure",
    "startup_failure": "failure",
}


class RestSnapshotter:
    name = "rest"

    def __init__(self, client: GitHubClient):
        self._client = client

    async def fetch(self, metas: list[RepoMeta]) -> dict[str, RepoDetails]:
        # One repository at a time: gentle on GitHub, and the feed shows steady progress.
        return {meta.full_name: await self.fetch_one(meta) for meta in metas}

    async def fetch_one(self, meta: RepoMeta) -> RepoDetails:
        base = f"/repos/{meta.full_name}"
        try:
            branch = quote(meta.default_branch, safe="")
            tree = (await self._client.get(f"{base}/git/trees/{branch}", {"recursive": 1})).data
        except (Conflict, NotFound):
            return RepoDetails(empty=True)

        entries = tree.get("tree") or []
        files = [e["path"] for e in entries if e.get("type") == "blob"]
        dirs = [e["path"] for e in entries if e.get("type") == "tree"]

        readme_path, readme_text = None, None
        if find_readme(files):
            readme_path, readme_text = await self.readme(meta)

        ci_state = None
        if any(WORKFLOW_RE.match(f) for f in files):
            ci_state = await self._ci_state(meta)

        return RepoDetails(
            tree_sha=tree.get("sha"),
            files=files,
            dirs=dirs,
            truncated=bool(tree.get("truncated")),
            readme_path=readme_path,
            readme_text=readme_text,
            ci_state=ci_state,
        )

    async def readme(self, meta: RepoMeta) -> tuple[str | None, str | None]:
        try:
            data = (await self._client.get(f"/repos/{meta.full_name}/readme")).data
        except NotFound:
            return None, None
        text = None
        if data.get("encoding") == "base64" and data.get("content"):
            text = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return data.get("path"), text

    async def _ci_state(self, meta: RepoMeta) -> CiState | None:
        params = {"branch": meta.default_branch, "per_page": 1, "exclude_pull_requests": "true"}
        try:
            data = (await self._client.get(f"/repos/{meta.full_name}/actions/runs", params)).data
        except (PermissionDenied, NotFound, AuthError):
            return None  # can't read Actions with this token: unknown, not failing
        runs = data.get("workflow_runs") or []
        if not runs:
            return None
        run = runs[0]
        if run.get("status") != "completed":
            return "pending"
        return _RUN_CONCLUSIONS.get(run.get("conclusion") or "", "neutral")


# -- looking inside: GraphQL ------------------------------------------------------------------

_README_ALIASES = {
    "readmeMd": "README.md",
    "readmeLower": "readme.md",
    "readmeBare": "README",
    "readmeRst": "README.rst",
}

_ROLLUP_STATES: dict[str, CiState] = {
    "SUCCESS": "success",
    "FAILURE": "failure",
    "ERROR": "failure",
    "PENDING": "pending",
    "EXPECTED": "pending",
}


def _tree_selection(depth: int) -> str:
    selection = "entries { name type }"
    for _ in range(depth - 1):
        selection = f"entries {{ name type object {{ ... on Tree {{ {selection} }} }} }}"
    return selection


def build_query(count: int, depth: int = GRAPHQL_TREE_DEPTH) -> str:
    variables = ", ".join(f"$o{i}: String!, $n{i}: String!" for i in range(count))
    repos = "\n".join(
        f"  r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...RepoFields }}" for i in range(count)
    )
    readmes = "\n".join(
        f'  {alias}: object(expression: "HEAD:{name}") {{ ... on Blob {{ text }} }}'
        for alias, name in _README_ALIASES.items()
    )
    return f"""query({variables}) {{
{repos}
}}
fragment RepoFields on Repository {{
  isEmpty
  latestRelease {{ tagName publishedAt }}
  defaultBranchRef {{
    target {{ ... on Commit {{ oid tree {{ oid }} statusCheckRollup {{ state }} }} }}
  }}
  root: object(expression: "HEAD:") {{ ... on Tree {{ {_tree_selection(depth)} }} }}
{readmes}
}}"""


def _flatten(entries: list[dict[str, Any]] | None, prefix: str, files: list[str], dirs: list[str]) -> None:
    for entry in entries or []:
        path = prefix + entry["name"]
        if entry.get("type") == "tree":
            dirs.append(path)
            _flatten((entry.get("object") or {}).get("entries"), path + "/", files, dirs)
        elif entry.get("type") == "blob":
            files.append(path)


def details_from_graphql(node: Mapping[str, Any], depth: int = GRAPHQL_TREE_DEPTH) -> RepoDetails:
    branch = node.get("defaultBranchRef")
    if node.get("isEmpty") or not branch:
        return RepoDetails(empty=True)

    target = branch.get("target") or {}
    files: list[str] = []
    dirs: list[str] = []
    _flatten((node.get("root") or {}).get("entries"), "", files, dirs)

    readme_path = find_readme(files)
    readme_text = None
    for alias, name in _README_ALIASES.items():
        if name == readme_path:
            readme_text = (node.get(alias) or {}).get("text")

    rollup = (target.get("statusCheckRollup") or {}).get("state")
    release = node.get("latestRelease") or {}
    return RepoDetails(
        head_sha=target.get("oid"),
        tree_sha=(target.get("tree") or {}).get("oid"),
        files=files,
        dirs=dirs,
        tree_depth=depth,
        readme_path=readme_path,
        readme_text=readme_text,
        ci_state=_ROLLUP_STATES.get(rollup or ""),
        latest_release=release.get("tagName"),
        released_at=release.get("publishedAt"),
    )


class GraphQLSnapshotter:
    name = "graphql"

    def __init__(self, client: GitHubClient):
        self._client = client
        self._rest = RestSnapshotter(client)

    async def fetch(self, metas: list[RepoMeta]) -> dict[str, RepoDetails]:
        out: dict[str, RepoDetails] = {}
        for start in range(0, len(metas), GRAPHQL_CHUNK):
            chunk = metas[start : start + GRAPHQL_CHUNK]
            variables: dict[str, str] = {}
            for i, meta in enumerate(chunk):
                variables[f"o{i}"] = meta.owner
                variables[f"n{i}"] = meta.name
            data = (await self._client.graphql(build_query(len(chunk)), variables))["data"]

            for i, meta in enumerate(chunk):
                node = data.get(f"r{i}")
                if node is None:
                    # GraphQL couldn't see this one (token scope, rename): ask REST instead.
                    out[meta.full_name] = await self._rest.fetch_one(meta)
                    continue
                details = details_from_graphql(node)
                if details.readme_path and details.readme_text is None:
                    # A README under a name the query didn't ask for.
                    path, text = await self._rest.readme(meta)
                    details = details.model_copy(
                        update={"readme_path": path or details.readme_path, "readme_text": text}
                    )
                out[meta.full_name] = details
        return out


async def fetch_details(client: GitHubClient, metas: list[RepoMeta]) -> tuple[dict[str, RepoDetails], str]:
    """Look inside the given repositories the cheapest way available. Returns details and the way used."""
    if not metas:
        return {}, "none"
    if client.authenticated:
        try:
            return await GraphQLSnapshotter(client).fetch(metas), "graphql"
        except (RateLimited, AuthError):
            raise
        except GitHubError:
            pass  # GraphQL unavailable for this token: REST gives the same answer in more requests
    return await RestSnapshotter(client).fetch(metas), "rest"
