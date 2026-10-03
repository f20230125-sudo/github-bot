"""GitHub REST and GraphQL client.

Every GET is conditional: it sends the ETag from last time, and an unchanged resource comes
back as 304 Not Modified. With a token, a 304 uses no rate-limit quota.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from ...db import Database
from ...events import utcnow

API_URL = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(GitHubError):
    """The token was rejected, or a token is needed and none is set."""


class PermissionDenied(GitHubError):
    """The token lacks a permission. `needed` is GitHub's own description of what it wants."""

    def __init__(self, message: str, needed: str | None = None):
        super().__init__(message, 403)
        self.needed = needed


class RateLimited(GitHubError):
    def __init__(self, message: str, reset_at: int | None = None):
        super().__init__(message, 429)
        self.reset_at = reset_at


class NotFound(GitHubError):
    pass


class Conflict(GitHubError):
    """409. For the tree endpoint this means the repository is empty."""


class WriteRefused(GitHubError):
    """Patch tried a write that isn't on its list. Nothing was sent."""


@dataclass(frozen=True)
class AllowedWrite:
    method: str
    pattern: re.Pattern[str]
    fields: frozenset[str]  # the only body fields that may be sent
    what: str  # in plain words, shown on Patch's page


# Every write Patch can make. There is no delete, no force-push, no visibility or settings change,
# and branches can only be created under patch/.
PATCH_BRANCH_PREFIX = "patch/"
_REPO = r"/repos/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+"
ALLOWED_WRITES: list[AllowedWrite] = [
    AllowedWrite("PATCH", re.compile(rf"^{_REPO}$"), frozenset({"description"}), "Set a repository's description"),
    AllowedWrite("PUT", re.compile(rf"^{_REPO}/topics$"), frozenset({"names"}), "Set a repository's topics"),
    AllowedWrite(
        "POST", re.compile(rf"^{_REPO}/git/trees$"), frozenset({"base_tree", "tree"}),
        "Prepare the files for a commit",
    ),
    AllowedWrite(
        "POST", re.compile(rf"^{_REPO}/git/commits$"), frozenset({"message", "tree", "parents"}),
        "Create that commit, not yet on any branch",
    ),
    AllowedWrite(
        "POST", re.compile(rf"^{_REPO}/git/refs$"), frozenset({"ref", "sha"}),
        "Create a new branch whose name starts with patch/",
    ),
    AllowedWrite(
        "POST", re.compile(rf"^{_REPO}/pulls$"), frozenset({"title", "head", "base", "body"}),
        "Open a pull request from that branch",
    ),
    # Only used when you switch on "merge after I approve". Off by default.
    AllowedWrite(
        "PUT", re.compile(rf"^{_REPO}/pulls/\d+/merge$"), frozenset({"merge_method"}),
        "Merge that pull request, only if you switch this on",
    ),
]  # fmt: skip
# What Patch has no call for at all, whatever a draft or a token would allow.
NEVER = (
    "Delete a repository, a branch or a file",
    "Force-push, or push to a default branch",
    "Change visibility, settings, collaborators or secrets",
    "Send anything to GitHub that is not on the list above",
)


def check_write(method: str, path: str, body: dict[str, Any]) -> None:
    if any(segment in (".", "..") for segment in path.split("/")):
        raise WriteRefused(f"{path} is not a path Patch writes to.")
    for allowed in ALLOWED_WRITES:
        pattern, fields = allowed.pattern, allowed.fields
        if method == allowed.method and pattern.match(path):
            extra = set(body) - fields
            if extra:
                raise WriteRefused(f"Patch may not send {', '.join(sorted(extra))} to {path}.")
            own_branch = f"refs/heads/{PATCH_BRANCH_PREFIX}"
            if path.endswith("/git/refs") and not str(body.get("ref", "")).startswith(own_branch):
                raise WriteRefused("Patch only creates branches under patch/.")
            if path.endswith("/pulls") and not str(body.get("head", "")).startswith(PATCH_BRANCH_PREFIX):
                raise WriteRefused("Patch only opens pull requests from its own patch/ branches.")
            return
    raise WriteRefused(f"{method} {path} is not something Patch is allowed to do.")


@dataclass(frozen=True)
class Fetched:
    data: Any
    next_url: str | None
    not_modified: bool


@dataclass(frozen=True)
class RequestInfo:
    method: str
    path: str
    status: int
    ms: int
    not_modified: bool
    remaining: int | None


Observer = Callable[[RequestInfo], Awaitable[None]]


class GitHubClient:
    def __init__(
        self,
        db: Database,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._db = db
        self._token = token or None
        self._identity = f"tok-{hashlib.sha256(token.encode()).hexdigest()[:12]}" if token else "anon"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "agent-desk-patch",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=API_URL, headers=headers, timeout=20.0, transport=transport)
        self.observer: Observer | None = None
        self.history: list[RequestInfo] = []  # every request this client made, in order

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- reads -------------------------------------------------------------------------------

    async def get(self, path: str, params: dict[str, str | int] | None = None) -> Fetched:
        url = path
        if params:
            url = f"{path}?{urlencode(sorted((k, str(v)) for k, v in params.items()))}"
        return await self._get(url)

    async def get_all(self, path: str, params: dict[str, str | int] | None = None) -> tuple[list[Any], bool]:
        """Follow pagination. Returns the items and whether every page was unchanged."""
        fetched = await self.get(path, params)
        items: list[Any] = list(fetched.data)
        unchanged = fetched.not_modified
        while fetched.next_url:
            fetched = await self._get(fetched.next_url)
            items.extend(fetched.data)
            unchanged = unchanged and fetched.not_modified
        return items, unchanged

    async def _get(self, url: str) -> Fetched:
        key = f"{self._identity}:{url}"
        cached = self._db.query_one("SELECT etag, body FROM http_cache WHERE key = ?", (key,))
        headers = {"If-None-Match": cached["etag"]} if cached else {}

        response, ms = await self._send("GET", url, headers=headers)
        await self._observe("GET", url, response, ms)

        if response.status_code == 304 and cached:
            body = json.loads(cached["body"])
            return Fetched(data=body["data"], next_url=body["next"], not_modified=True)

        self._raise_for_status(response)
        data = response.json()
        next_url = response.links.get("next", {}).get("url")
        etag = response.headers.get("etag")
        if etag:
            self._db.execute(
                "INSERT INTO http_cache (key, etag, body, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET etag = excluded.etag, body = excluded.body, "
                "fetched_at = excluded.fetched_at",
                (key, etag, json.dumps({"data": data, "next": next_url}), utcnow()),
            )
        return Fetched(data=data, next_url=next_url, not_modified=False)

    async def get_file_text(self, full_name: str, path: str) -> str | None:
        """The text of one file on the default branch, or None if it's missing, binary or too large."""
        try:
            data = (await self.get(f"/repos/{full_name}/contents/{quote(path)}")).data
        except NotFound:
            return None
        if not isinstance(data, dict) or data.get("encoding") != "base64" or not data.get("content"):
            return None
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    async def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Returns the whole body: `data`, plus `errors` when some fields failed."""
        if not self.authenticated:
            raise AuthError("GitHub's GraphQL API needs a token.")
        response, ms = await self._send("POST", "/graphql", json={"query": query, "variables": variables})
        await self._observe("POST", "/graphql", response, ms)
        self._raise_for_status(response)
        body = response.json()
        if body.get("data") is None:
            messages = "; ".join(e.get("message", "") for e in body.get("errors", [])) or "no data returned"
            raise GitHubError(f"GraphQL query failed: {messages}")
        return body

    # -- writes ------------------------------------------------------------------------------

    async def write(self, method: str, path: str, body: dict[str, Any]) -> Any:
        """Send one of the few writes Patch is allowed to make. Anything else is refused here,
        before a request exists, whatever asked for it."""
        check_write(method, path, body)
        if not self.authenticated:
            raise AuthError("Changing anything on GitHub needs a token. Add one in Setup.")
        response, ms = await self._send(method, path, json=body)
        await self._observe(method, path, response, ms)
        self._raise_for_status(response)
        return response.json() if response.content else None

    # -- plumbing ----------------------------------------------------------------------------

    async def _send(self, method: str, url: str, **kwargs: Any) -> tuple[httpx.Response, int]:
        started = time.perf_counter()
        try:
            response = await self._http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"Could not reach GitHub ({exc.__class__.__name__}).") from exc
        return response, int((time.perf_counter() - started) * 1000)

    async def _observe(self, method: str, url: str, response: httpx.Response, ms: int) -> None:
        remaining = response.headers.get("x-ratelimit-remaining")
        info = RequestInfo(
            method=method,
            path=url.removeprefix(API_URL),
            status=response.status_code,
            ms=ms,
            not_modified=response.status_code == 304,
            remaining=int(remaining) if remaining and remaining.isdigit() else None,
        )
        self.history.append(info)
        if self.observer is not None:
            await self.observer(info)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        try:
            message = str(response.json().get("message", "")) or response.reason_phrase
        except ValueError:
            message = response.reason_phrase

        if status == 401:
            raise AuthError("GitHub rejected the token.", status)
        if status in (403, 429):
            headers = response.headers
            if headers.get("retry-after") or headers.get("x-ratelimit-remaining") == "0":
                reset = headers.get("x-ratelimit-reset")
                raise RateLimited("GitHub rate limit reached.", int(reset) if reset and reset.isdigit() else None)
            needed = headers.get("x-accepted-github-permissions")
            if needed:
                raise PermissionDenied(f"The token is missing a permission: {needed}.", needed)
            raise GitHubError(f"GitHub refused the request: {message}", status)
        if status == 404:
            raise NotFound(f"Not found: {message}", status)
        if status == 409:
            raise Conflict(message, status)
        raise GitHubError(f"GitHub returned {status}: {message}", status)
