"""A small stand-in for GitHub's API that honours ETags and records every request."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.agents.github.sync import _README_ALIASES, GRAPHQL_TREE_DEPTH

VALID_TOKEN = "github_pat_" + "x" * 40

GOOD_README = """# Sample project

A small service that answers questions about internal documents. It retrieves the passages that
matter, answers from them, and returns the sources it used so every answer can be checked.

![screenshot](docs/shot.png)

## Setup

```bash
pip install -r requirements.txt
```

## Usage

Run `python main.py`, open the page, and ask a question. The answer comes back with its sources.

## Tech stack

Python and FastAPI, with a vector index for retrieval and pytest for the tests.
"""


@dataclass
class FakeRepo:
    name: str
    owner: str = "octo"
    description: str | None = "A project."
    homepage: str | None = "https://example.com"
    topics: list[str] = field(default_factory=lambda: ["python", "fastapi", "demo"])
    license: str | None = "MIT"
    language: str | None = "Python"
    pushed_at: str = "2026-09-01T00:00:00Z"
    default_branch: str = "main"
    files: dict[str, str] = field(default_factory=dict)
    ci: str | None = None  # "success", "failure" or "in_progress"
    private: bool = False
    fork: bool = False
    archived: bool = False
    stars: int = 0
    empty: bool = False
    release: tuple[str, str] | None = None  # (tag, published at)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def healthy_files() -> dict[str, str]:
    """Files for a repository that passes every check."""
    return {
        "README.md": GOOD_README,
        "LICENSE": "MIT License",
        ".gitignore": "__pycache__/",
        "requirements.txt": "fastapi",
        "main.py": "print('hi')",
        "docs/shot.png": "png",
        "tests/test_main.py": "def test_ok(): pass",
        ".github/workflows/ci.yml": "name: CI",
    }


@dataclass(frozen=True)
class Call:
    method: str
    path: str
    status: int


@dataclass(frozen=True)
class Write:
    method: str
    path: str
    body: dict[str, Any]


class FakeGitHub:
    def __init__(self, repos: list[FakeRepo], user: str = "octo", profile_name: str | None = "Octo Cat"):
        self.repos = {r.name: r for r in repos}
        self.user = user
        self.profile_name = profile_name
        self.calls: list[Call] = []
        self.rate_limited = False
        self.graphql_broken = False
        # Writing: what the token may do, and everything that was written.
        self.permissions = {"contents", "pull_requests", "administration"}
        self.merge_blocked = False
        self.writes: list[Write] = []
        self.pulls: list[dict[str, Any]] = []
        self._trees: dict[str, dict[str, str]] = {}
        self._commits: dict[str, str] = {}
        self._refs: dict[tuple[str, str], str] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def reset(self) -> None:
        self.calls.clear()

    def statuses(self) -> list[int]:
        return [c.status for c in self.calls]

    # -- routing ------------------------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        response = self._route(request)
        self.calls.append(Call(request.method, request.url.path, response.status_code))
        return response

    def _route(self, request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        authed = auth is not None
        if authed and auth != f"Bearer {VALID_TOKEN}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.rate_limited:
            headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}
            return httpx.Response(403, json={"message": "API rate limit exceeded"}, headers=headers)

        path = request.url.path
        parts = path.strip("/").split("/")

        if request.method == "POST" and path == "/graphql":
            if self.graphql_broken:
                return httpx.Response(200, json={"data": None, "errors": [{"message": "Resource not accessible"}]})
            return self._graphql(json.loads(request.content))
        if request.method != "GET":
            return self._write(request, parts, authed)

        if path == "/user":
            return self._json(request, {"login": self.user}) if authed else httpx.Response(401, json={"message": "Requires authentication"})
        if path == "/user/repos" and authed:
            return self._json(request, [self._listing(r) for r in self._sorted()])
        if path == f"/users/{self.user}/repos":
            return self._json(request, [self._listing(r) for r in self._sorted() if not r.private])
        if path == f"/users/{self.user}":
            return self._json(request, {"login": self.user, "name": self.profile_name})

        if len(parts) >= 3 and parts[0] == "repos":
            repo = self.repos.get(parts[2])
            if repo is None or parts[1] != repo.owner:
                return httpx.Response(404, json={"message": "Not Found"})
            rest = "/".join(parts[3:])
            if rest.startswith("git/trees/"):
                if repo.empty:
                    return httpx.Response(409, json={"message": "Git Repository is empty."})
                return self._json(request, self._tree(repo))
            if rest.startswith("git/ref/heads/"):
                return self._json(request, {"ref": f"refs/{rest.removeprefix('git/ref/')}", "object": {"sha": self._commit_sha(repo)}})
            if rest.startswith("git/commits/"):
                return self._json(request, {"sha": rest.removeprefix("git/commits/"), "tree": {"sha": self._tree_sha(repo)}})
            if rest == "readme":
                name = next((f for f in repo.files if "/" not in f and f.lower().startswith("readme")), None)
                if name is None:
                    return httpx.Response(404, json={"message": "Not Found"})
                content = base64.encodebytes(repo.files[name].encode()).decode()
                return self._json(request, {"path": name, "encoding": "base64", "content": content})
            if rest.startswith("contents/"):
                file_path = rest.removeprefix("contents/")
                if file_path not in repo.files:
                    return httpx.Response(404, json={"message": "Not Found"})
                content = base64.encodebytes(repo.files[file_path].encode()).decode()
                return self._json(request, {"path": file_path, "encoding": "base64", "content": content})
            if rest == "actions/runs":
                runs = []
                if repo.ci == "in_progress":
                    runs = [{"status": "in_progress", "conclusion": None}]
                elif repo.ci:
                    runs = [{"status": "completed", "conclusion": repo.ci}]
                return self._json(request, {"total_count": len(runs), "workflow_runs": runs})
        return httpx.Response(404, json={"message": "Not Found"})

    # -- writes -------------------------------------------------------------------------------

    def _denied(self, permission: str) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Resource not accessible by personal access token"},
            headers={"x-accepted-github-permissions": f"{permission}=write", "x-ratelimit-remaining": "4990"},
        )

    def _write(self, request: httpx.Request, parts: list[str], authed: bool) -> httpx.Response:
        if not authed:
            return httpx.Response(401, json={"message": "Requires authentication"})
        repo = self.repos.get(parts[2]) if len(parts) >= 3 and parts[0] == "repos" else None
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        body = json.loads(request.content or b"{}")
        rest = "/".join(parts[3:])
        method = request.method
        self.writes.append(Write(method, request.url.path, body))

        if method == "PATCH" and rest == "":
            if "administration" not in self.permissions:
                return self._denied("administration")
            repo.description = body.get("description", repo.description)
            return httpx.Response(200, json=self._listing(repo))
        if method == "PUT" and rest == "topics":
            if "administration" not in self.permissions:
                return self._denied("administration")
            repo.topics = list(body["names"])
            return httpx.Response(200, json={"names": repo.topics})

        if rest.startswith("git/") and "contents" not in self.permissions:
            return self._denied("contents")
        if method == "POST" and rest == "git/trees":
            sha = f"tree{len(self._trees) + 1:036d}"
            self._trees[sha] = {entry["path"]: entry["content"] for entry in body["tree"]}
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and rest == "git/commits":
            sha = f"commit{len(self._commits) + 1:034d}"
            self._commits[sha] = body["tree"]
            return httpx.Response(201, json={"sha": sha})
        if method == "POST" and rest == "git/refs":
            key = (repo.name, body["ref"])
            if key in self._refs:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self._refs[key] = body["sha"]
            return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})

        if method == "POST" and rest == "pulls":
            if "pull_requests" not in self.permissions:
                return self._denied("pull_requests")
            commit = self._refs.get((repo.name, f"refs/heads/{body['head']}"))
            number = len(self.pulls) + 1
            self.pulls.append(
                {
                    "repo": repo.name, "number": number, "title": body["title"], "body": body["body"],
                    "head": body["head"], "base": body["base"], "files": self._trees.get(self._commits.get(commit, ""), {}),
                    "merged": False,
                }
            )  # fmt: skip
            return httpx.Response(201, json={"number": number, "html_url": f"https://github.com/{repo.full_name}/pull/{number}"})
        if method == "PUT" and rest.startswith("pulls/") and rest.endswith("/merge"):
            if self.merge_blocked:
                return httpx.Response(405, json={"message": "Required status check is expected."})
            pull = next(p for p in self.pulls if p["repo"] == repo.name and p["number"] == int(parts[4]))
            repo.files.update(pull["files"])
            repo.pushed_at = "2026-10-01T00:00:00Z"
            if "LICENSE" in pull["files"]:
                repo.license = "MIT"
            pull["merged"] = True
            return httpx.Response(200, json={"merged": True})
        return httpx.Response(404, json={"message": "Not Found"})

    # -- payloads -----------------------------------------------------------------------------

    def _sorted(self) -> list[FakeRepo]:
        return sorted(self.repos.values(), key=lambda r: r.full_name.lower())

    def _json(self, request: httpx.Request, payload: Any) -> httpx.Response:
        etag = '"' + hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16] + '"'
        headers = {"etag": etag, "x-ratelimit-remaining": "4999"}
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, json=payload, headers=headers)

    @staticmethod
    def _listing(repo: FakeRepo) -> dict[str, Any]:
        return {
            "full_name": repo.full_name,
            "name": repo.name,
            "owner": {"login": repo.owner},
            "html_url": f"https://github.com/{repo.full_name}",
            "description": repo.description,
            "homepage": repo.homepage,
            "topics": repo.topics,
            "license": {"spdx_id": repo.license} if repo.license else None,
            "default_branch": repo.default_branch,
            "pushed_at": repo.pushed_at,
            "private": repo.private,
            "archived": repo.archived,
            "fork": repo.fork,
            "stargazers_count": repo.stars,
            "open_issues_count": 0,
            "language": repo.language,
            "size": len(repo.files),
        }

    @staticmethod
    def _tree_sha(repo: FakeRepo) -> str:
        return hashlib.sha1(json.dumps(repo.files, sort_keys=True).encode()).hexdigest()

    def _commit_sha(self, repo: FakeRepo) -> str:
        return "c" + self._tree_sha(repo)[:39]

    def _tree(self, repo: FakeRepo) -> dict[str, Any]:
        dirs: set[str] = set()
        for path in repo.files:
            parts = path.split("/")
            dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
        entries = [{"path": p, "type": "blob"} for p in sorted(repo.files)]
        entries += [{"path": d, "type": "tree"} for d in sorted(dirs)]
        return {"sha": self._tree_sha(repo), "tree": entries, "truncated": False}

    def _graphql(self, body: dict[str, Any]) -> httpx.Response:
        variables = body["variables"]
        data: dict[str, Any] = {}
        i = 0
        while f"n{i}" in variables:
            repo = self.repos.get(variables[f"n{i}"])
            data[f"r{i}"] = self._node(repo) if repo and repo.owner == variables[f"o{i}"] else None
            i += 1
        return httpx.Response(200, json={"data": data}, headers={"x-ratelimit-remaining": "4998"})

    def _node(self, repo: FakeRepo) -> dict[str, Any]:
        if repo.empty:
            return {"isEmpty": True, "defaultBranchRef": None, "root": None}

        nested: dict[str, Any] = {}
        for path in repo.files:
            node = nested
            *folders, name = path.split("/")
            for folder in folders:
                node = node.setdefault(folder, {})
            node[name] = None

        def entries(node: dict[str, Any], level: int) -> list[dict[str, Any]]:
            out = []
            for name, child in sorted(node.items()):
                if isinstance(child, dict):
                    entry: dict[str, Any] = {"name": name, "type": "tree"}
                    if level < GRAPHQL_TREE_DEPTH:
                        entry["object"] = {"entries": entries(child, level + 1)}
                    out.append(entry)
                else:
                    out.append({"name": name, "type": "blob"})
            return out

        rollup = {"success": "SUCCESS", "failure": "FAILURE", "in_progress": "PENDING"}.get(repo.ci or "")
        result: dict[str, Any] = {
            "isEmpty": False,
            "latestRelease": {"tagName": repo.release[0], "publishedAt": repo.release[1]} if repo.release else None,
            "defaultBranchRef": {
                "target": {
                    "oid": "c" * 40,
                    "tree": {"oid": self._tree_sha(repo)},
                    "statusCheckRollup": {"state": rollup} if rollup else None,
                }
            },
            "root": {"entries": entries(nested, 1)},
        }
        for alias, name in _README_ALIASES.items():
            result[alias] = {"text": repo.files[name]} if name in repo.files else None
        return result
