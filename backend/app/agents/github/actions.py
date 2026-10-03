"""The only code that changes anything on GitHub.

Three named actions, nothing else: set a description, set topics, open a pull request from a
patch/ branch. Each runs only for a proposal you approved. The client refuses any other write.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import quote

from .client import PATCH_BRANCH_PREFIX, GitHubClient


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str


class GitHubWriter:
    def __init__(self, client: GitHubClient, delay: float = 1.0):
        self._client = client
        self._delay = delay  # GitHub asks for a pause between writes
        self._wrote = False

    async def _write(self, method: str, path: str, body: dict) -> dict:
        if self._wrote and self._delay:
            await asyncio.sleep(self._delay)
        self._wrote = True
        return await self._client.write(method, path, body)

    async def set_description(self, full_name: str, description: str) -> None:
        await self._write("PATCH", f"/repos/{full_name}", {"description": description})

    async def set_topics(self, full_name: str, topics: list[str]) -> None:
        await self._write("PUT", f"/repos/{full_name}/topics", {"names": topics})

    async def head(self, full_name: str, branch: str) -> tuple[str, str]:
        """The commit and tree the branch points at right now."""
        ref = (await self._client.get(f"/repos/{full_name}/git/ref/heads/{quote(branch, safe='')}")).data
        commit_sha = ref["object"]["sha"]
        commit = (await self._client.get(f"/repos/{full_name}/git/commits/{commit_sha}")).data
        return commit_sha, commit["tree"]["sha"]

    async def open_pull_request(
        self,
        full_name: str,
        *,
        base_branch: str,
        base_commit: str,
        base_tree: str,
        branch: str,
        title: str,
        body: str,
        files: list[tuple[str, str]],
    ) -> PullRequest:
        """One commit with every file, on a new patch/ branch, then a pull request. Never the default branch."""
        if not branch.startswith(PATCH_BRANCH_PREFIX):
            raise ValueError("Patch only works on its own patch/ branches.")
        base = f"/repos/{full_name}"
        entries = [{"path": path, "mode": "100644", "type": "blob", "content": content} for path, content in files]
        tree = await self._write("POST", f"{base}/git/trees", {"base_tree": base_tree, "tree": entries})
        commit = await self._write(
            "POST", f"{base}/git/commits", {"message": title, "tree": tree["sha"], "parents": [base_commit]}
        )
        await self._write("POST", f"{base}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        pull = await self._write(
            "POST", f"{base}/pulls", {"title": title, "head": branch, "base": base_branch, "body": body}
        )
        return PullRequest(number=pull["number"], url=pull["html_url"], branch=branch)

    async def merge(self, full_name: str, number: int) -> None:
        """Squash-merge a pull request Patch just opened. Only when "merge after I approve" is on."""
        await self._write("PUT", f"/repos/{full_name}/pulls/{number}/merge", {"merge_method": "squash"})
